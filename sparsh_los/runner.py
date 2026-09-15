# Copyright (c) 2026, SSSIHMS and contributors
# For license information, please see license.txt

"""The activity runner: instruction -> response -> evaluate -> hint -> retry -> evidence.

One generic loop drives every activity type. Adding an activity type means adding an
evaluator, never a new screen and never a change here.

Nothing in this module calls a model or a network service. Evaluation is deterministic
and the hint ladder is stored text, so an ordinary practice session costs nothing and
works when no AI service is reachable.

The learner supplies only their response. Outcome, critical-error status and evidence
are decided here, server-side, which is why this module — and not the caller — sets
them on the Attempt.
"""

import re
import unicodedata
from decimal import Decimal, InvalidOperation

import frappe
from frappe import _

from sparsh_los import events

from sparsh_los.permissions import throttle

MAX_HINT_LEVEL = 4


def _require_enrolment():
	roles = set(frappe.get_roles(frappe.session.user))
	if not roles & {"Sparsh Learner", "Sparsh Reviewer", "System Manager", "Administrator"}:
		frappe.throw(_("You are not enrolled in this programme"), frappe.PermissionError)


def _activity(name):
	return frappe.get_cached_doc("Sparsh Activity", name)


# Punctuation and unicode dashes/quotes are stripped before matching: "stop the
# medicine." slipped past a marker for "stop the medicine", which is not a
# distinction anybody intended to make.
_PUNCTUATION = re.compile(r"[^\w\s]", re.UNICODE)


def _normalise(text):
	text = unicodedata.normalize("NFKC", text or "")
	text = _PUNCTUATION.sub(" ", text)
	return " ".join(text.strip().lower().split())


def _accepted_responses(activity):
	return [_normalise(line) for line in (activity.expected_response or "").splitlines() if line.strip()]


def _governing_rule(competency):
	"""The competency's current rule, so the attempt records what it was judged under.

	Without this every runner attempt stored rule_version 0 and the immutable
	snapshot identified nothing.
	"""
	if not competency:
		return None

	links = frappe.get_all(
		"Sparsh Competency Rule Link", filters={"parent": competency}, fields=["rule"], pluck="rule"
	)
	if not links:
		return None

	# Highest version among the rules that are not superseded. Taking whichever link
	# came first made the recorded governing rule depend on authoring order.
	candidates = [
		(frappe.db.get_value("Sparsh Source of Truth Rule", r, "version") or 0, r)
		for r in links
		if frappe.db.get_value("Sparsh Source of Truth Rule", r, "status") != "Superseded"
	]
	if candidates:
		return max(candidates)[1]

	return sorted(links)[0]


def _lines(text):
	return [line.strip() for line in (text or "").splitlines() if line.strip()]


def _hint_for(activity, level):
	"""The hint at `level`, or the strongest below it. Line 1 is level 1."""
	hints = _lines(activity.hints)
	if not hints or level < 1:
		return None

	return hints[min(level, len(hints)) - 1]


NEGATIONS = ("not", "never", "dont", "don't", "avoid", "without", "shouldnt", "shouldn't")


def _is_critical(activity, response):
	"""True when the response matches one of the activity's declared critical errors.

	Matching is on word boundaries and skips a match that is negated just before it:
	plain substring matching flagged "do not stop the medicine" as unsafe, which is
	the opposite of what the volunteer said. This is a blunt instrument either way —
	free-text safety detection is advisory, which is why every critical result is
	routed to a person rather than left to stand on its own.
	"""
	answer = _normalise(response)
	if not answer:
		return False

	words = answer.split()
	for marker_text in _lines(activity.critical_markers):
		marker = _normalise(marker_text)
		if not marker:
			continue

		marker_words = marker.split()
		for i in range(len(words) - len(marker_words) + 1):
			if words[i : i + len(marker_words)] != marker_words:
				continue

			# Immediately preceding only. A wider window read "do not hesitate to stop
			# the medicine" as a negation, which it is not.
			if i > 0 and words[i - 1] in NEGATIONS:
				continue

			return True

	return False


# The evaluator types the programme owner asked to be explicit from the start, so a new
# activity type does not need a new screen or a change to the learner data model.
# `AUTO_SCORED_MODES` is an allow-list, not a denylist: anything outside it -- an unbuilt
# mode, an empty field, a value written straight to the column -- goes to a person. Declaring a mode the engine cannot perform and silently scoring it
# with the deterministic comparison would be the worst of both.
#
# The tuple names the modes the *string comparison* may score. Numeric validation is
# built and scores itself, but through its own arithmetic branch below, never through
# the comparison -- so it stays out of this tuple, and the harness check that iterates
# every declared mode outside it keeps proving that nothing falls through to
# `expected_response`.
AUTO_SCORED_MODES = ("Deterministic",)
# Scored by arithmetic against `expected_value` and `tolerance`, under the same rule
# gate as Deterministic. Not language: a response that holds no single number is not
# an answer, and `evaluate` refuses it rather than grading it (see `NotAnAnswer`).
NUMERIC_MODE = "Numeric validation"
# Judged by a person, aggregated by the engine: the reviewer names which of the
# activity's `rubric_criteria` were met and `review.score_rubric` turns that into
# Pass / Partial / Fail. `evaluate` never scores it; the attempt waits for a person.
RUBRIC_MODE = "Rubric"
# A Reflection is the learner's own written thinking, stored for their record. It is
# not work awaiting a verdict, so it produces no Evidence and never reaches the review
# queue -- `review.pending` and `review.record_evidence` both consult this tuple. It
# was declared and read by nothing, so a Reflection fell through the same path as an
# unbuilt evaluator and sat in front of a reviewer as if it needed judging.
NOT_SCORED_MODES = ("Reflection",)
# Declared in the Select, dispatched by nothing yet. `evaluate` returns Not Evaluated
# for these and a person judges the attempt through `review.record_evidence`.
AWAITING_IMPLEMENTATION_MODES = ("AI-assisted",)


class NotAnAnswer(frappe.ValidationError):
	"""A response to a Numeric activity that holds no single number.

	Not a wrong answer -- not an answer. It is refused before an Attempt exists, so it
	neither climbs the hint ladder nor counts as a retry, and it never sits in front of
	a reviewer as free text on an activity that asked for a number.
	"""


# One number, optionally signed, optionally with a decimal part. Thousands separators
# are deliberately not understood: "1,200" is two numbers here, and "1,5" would be a
# European decimal that the same rule would misread as one thousand five hundred.
# Whatever else the response carries -- a unit, a word -- is ignored, so the activity
# instruction must name the unit it wants and `expected_value` must be in that unit.
_NUMBER = re.compile(r"[-+]?(?:\d+\.?\d*|\.\d+)")


def _numeric_value(response):
	"""The one number in a response as a Decimal, or None when there is not exactly one.

	Decimal rather than float: 2.7 - 2.5 in binary floating point is a shade over 0.2,
	so a tolerance of 0.2 would refuse a response the author meant to accept.
	"""
	found = _NUMBER.findall(unicodedata.normalize("NFKC", response or ""))
	if len(found) != 1:
		return None

	try:
		return Decimal(found[0])
	except InvalidOperation:
		return None


def _evaluate_numeric(activity, response):
	"""Absolute tolerance: |response - expected| <= tolerance, in the instruction's unit.

	Absolute rather than relative because the values an activity asks for -- a
	weight, a count, a reading, a dose -- are stated in a unit, and an author thinks
	of "within 0.5 kg", not "within 2 percent". A relative tolerance is also
	meaningless at an expected value of zero, which is a legitimate answer. If an
	activity ever needs a relative band it is one more permlevel-1 field, not a
	change to this rule.

	`expected_value` is Data, not Float, for two reasons: a Float column stores blank
	as 0, so "no answer agreed yet" and "the answer is zero" would be the same row;
	and the text the author typed parses exactly, where a float would not.
	"""
	expected = _numeric_value(activity.expected_value)
	if expected is None:
		# Nothing to compare against is a content gap, not a learner failure --
		# the same rule Deterministic applies to an empty `expected_response`.
		return "Not Evaluated", 0

	value = _numeric_value(response)
	if value is None:
		frappe.throw(
			_("This activity expects a single number. Enter one number in the unit the instruction names."),
			NotAnAnswer,
		)

	tolerance = Decimal(str(activity.tolerance or 0))
	return ("Pass", 0) if abs(value - expected) <= tolerance else ("Fail", 0)


# The automation statuses that permit a machine to apply a rule. "AI may advise" is
# included because advising is what an automatic score is here: the outcome still
# reaches a person through the review queue. "Human review required" and "Do not
# automate" are refusals and are absent deliberately.
AUTOMATABLE = ("Safe as fixed logic", "AI may advise")


def _rule_is_validated(competency):
	"""True only when a rule that permits automation governs this competency.

	Every non-superseded linked rule must be Validated, not merely the highest-versioned
	one: checking only the governing rule meant a competency linked to a Validated v2
	and a Draft v1 auto-scored, because the gate happened to look at v2.

	Two holes an independent audit found, both now closed.

	A competency with **no rule linked** used to pass. That made forgetting a link a
	way to authorise automatic scoring, which is the opposite of what §8 asks: nothing
	is automated until a rule says it may be. It now fails closed, and the work goes to
	a person instead. `seed.programme_readiness` still names these so the content gap
	is visible rather than merely inert.

	`status` was the only field read. A rule can be Validated -- the owner has ruled on
	the wording -- and still carry `automation_status` of "Human review required" or
	"Do not automate", which is a separate decision about whether a machine may apply
	it. Ruling on wording is not permission to automate, and the two are now both
	required.
	"""
	if not competency:
		return False

	links = frappe.get_all(
		"Sparsh Competency Rule Link", filters={"parent": competency}, pluck="rule"
	)
	if not links:
		return False

	for rule in links:
		row = frappe.db.get_value(
			"Sparsh Source of Truth Rule", rule, ["status", "automation_status"], as_dict=True
		)
		if not row or row.status not in ("Validated", "Superseded"):
			return False
		if row.automation_status not in AUTOMATABLE:
			return False

	return True


def evaluate(activity, response):
	"""Return (outcome, critical_error). Deterministic; no model call.

	Deterministic scores by string comparison and Numeric validation by arithmetic;
	every other mode returns Not Evaluated and waits for a reviewer. That is the right
	answer for judgement-heavy work -- a Rubric attempt is scored by
	`review.score_rubric` once a person has said which criteria were met -- and for
	the modes not yet built it is the only safe one: the alternative is falling
	through to the string comparison, which would score a free-text answer as though
	it were a multiple-choice question.
	"""
	# The rule gate comes first, ahead of anything the engine might conclude -- the
	# safety branch included. Critical markers used to be checked before it, so an
	# activity governed by a Draft rule still auto-failed on safety, and critical
	# Evidence cannot be cancelled: the one judgement an unvalidated rule could still
	# make was the irreversible one. `submit` still escalates such a response to a
	# person; what it no longer does is impose a permanent block on the authority of a
	# rule nobody has validated.
	if not _rule_is_validated(activity.competency):
		return "Not Evaluated", 0

	# A Reflection is stored, not judged, so it is settled before the safety branch:
	# critical Evidence cannot be cancelled, and a learner's own reflection is not a
	# demonstration that should carry a permanent block. `submit` still puts a
	# reflection that matches a critical marker in front of a person, through the same
	# escalation the unvalidated-rule path uses -- what it does not do is write Evidence.
	if (activity.evaluation_mode or "") in NOT_SCORED_MODES:
		return "Not Evaluated", 0

	# Under a validated rule a critical marker bites whatever the mode: an unsafe
	# answer is unsafe whether or not the engine can grade the rest of it.
	if _is_critical(activity, response):
		return "Fail", 1

	mode = activity.evaluation_mode or "Human review"
	if mode == NUMERIC_MODE:
		# Its own branch, ahead of the allow-list: arithmetic, not the string
		# comparison, and it may refuse the response outright (`NotAnAnswer`).
		return _evaluate_numeric(activity, response)

	if mode not in AUTO_SCORED_MODES:
		return "Not Evaluated", 0

	accepted = _accepted_responses(activity)
	if not accepted:
		# Nothing to compare against is a content gap, not a learner failure.
		return "Not Evaluated", 0

	return ("Pass", 0) if _normalise(response) in accepted else ("Fail", 0)


@frappe.whitelist()
def start(activity):
	"""Open a session on one activity, with the learner's position on the ladder.

	It returned "the instruction and nothing more" until a reviewer's Rubric verdict
	became able to issue a hint; the docstring is kept honest because nine defects in
	this project were first described accurately by a comment beside code that did not
	do it.
	"""
	_require_enrolment()
	doc = _activity(activity)
	events.emit(
		events.ACTIVITY_STARTED,
		learner=frappe.session.user,
		competency=doc.competency,
		activity=doc.name,
	)
	# The learner's real position, not a placeholder: a hint issued when a reviewer
	# scored a Rubric attempt has no other way to reach them, because the verdict
	# arrives after the session that produced the attempt has closed. `hint` is the
	# strongest rung already shown -- never the next one.
	hint_level, retry_index = _session_position(frappe.session.user, doc.name)
	return {
		"activity": doc.name,
		"title": doc.title,
		"instruction": doc.instruction,
		"competency": doc.competency,
		"hint_level": hint_level,
		"retry_index": retry_index,
		"hint": _hint_for(doc, hint_level),
	}


def _session_position(learner, activity):
	"""Assistance level and retry index, counted from the record, not from the caller.

	Assistance never falls. Once a learner has been shown a hint for an activity,
	every later answer on it is assisted: resetting the count after a pass let them
	take a hint, pass with it, and immediately resubmit the now-known answer as an
	independent pass. Demonstrating unaided competence needs a different activity.

	Concurrency is a real limit here, not a solved problem. Two submissions racing
	can both read the same position, and the one that commits second can record less
	assistance than the learner has by then been shown. The window is the gap between
	this read and the insert, which is why the read happens immediately before the
	write rather than at the start of the request. Closing it properly needs a lock
	on (learner, activity), which is a schema decision rather than a patch.
	"""
	attempts = frappe.get_all(
		"Sparsh Attempt",
		filters={"learner": learner, "activity": activity},
		fields=["name", "outcome", "hint_level_used"],
		order_by="creation asc",
	)

	failures = sum(1 for row in attempts if row.outcome not in ("Pass", "Not Evaluated"))
	seen = max((row.hint_level_used or 0) for row in attempts) if attempts else 0

	# A verdict a person gave on a Not Evaluated attempt is a failure the attempt
	# itself does not record -- attempts are immutable, and the engine returned the
	# next hint when the verdict was written (`review.score_rubric`,
	# `review.record_evidence`). Without this a Rubric learner could be handed hint
	# after hint and still submit each new answer as unaided.
	waiting = [row.name for row in attempts if row.outcome == "Not Evaluated"]
	if waiting:
		# `critical_error: 0` matters. A verdict carrying a critical error issues no
		# hint -- the response went to a reviewer and the learner was told nothing about
		# how to do better -- so counting it here raised the assistance level for help
		# that was never given. Assistance must never fall, but it must also record what
		# the learner was actually shown: an unaided pass wrongly marked assisted is a
		# volunteer denied credit for competence they demonstrated.
		failures += frappe.db.count(
			"Sparsh Evidence",
			{
				"attempt": ("in", waiting),
				"outcome": ("in", ("Partial", "Fail")),
				"critical_error": 0,
				"docstatus": 1,
			},
		)

	return min(max(failures, seen), MAX_HINT_LEVEL), len(attempts)


@frappe.whitelist()
def submit(activity, response):
	"""Record one response, evaluate it, and return the minimum help required.

	A pass produces Evidence, which recomputes mastery. A failure returns the next
	rung of the hint ladder and invites a retry.

	The ladder stops at the strongest hint the activity stores. It does **not** reveal
	`expected_response` at the top, though this docstring claimed it did: `_hint_for`
	only ever returns authored hints. Section 14 of the build guide asks for the
	preferred answer after repeated difficulty, so the engine is short of the
	specification here -- and with hints present `can_retry` stays True for ever at the
	top rung. Recorded rather than fixed silently: what to show, and whether showing it
	should end the attempt, is a programme decision.
	"""
	# Enrolment first, exactly as `start` does it. Looking the activity up ahead of the
	# gate let an unenrolled account tell a real activity name from a fabricated one by
	# which error came back -- a small oracle, but one that leaks the content catalogue
	# to precisely the accounts that may not read it.
	_require_enrolment()

	doc = _activity(activity)
	learner = frappe.session.user

	throttle("Sparsh Attempt")
	hint_level, retry_index = _session_position(learner, activity)

	outcome, critical_error = evaluate(doc, response)

	attempt = frappe.new_doc("Sparsh Attempt")
	attempt.learner = learner
	attempt.activity = doc.name
	attempt.rule = _governing_rule(doc.competency)
	attempt.response = response
	attempt.hint_level_used = hint_level
	attempt.retry_index = retry_index
	attempt.outcome = outcome
	attempt.critical_error = critical_error

	# The runner decides the verdict, so the learner-input limits do not apply to it.
	# The learner never supplies `outcome`; it is computed above from stored content.
	previous_flag = frappe.flags.in_sparsh_runner
	frappe.flags.in_sparsh_runner = True
	try:
		attempt.insert(ignore_permissions=True)
	finally:
		frappe.flags.in_sparsh_runner = previous_flag

	events.emit(
		events.ACTIVITY_COMPLETED,
		learner=learner,
		competency=doc.competency,
		activity=doc.name,
		detail=f"outcome={outcome} assistance={hint_level} retry={retry_index}",
		reference_doctype="Sparsh Attempt",
		reference_name=attempt.name,
	)
	if retry_index:
		events.emit(
			events.RETRY_MADE,
			learner=learner,
			competency=doc.competency,
			activity=doc.name,
			detail=f"retry={retry_index}",
		)
	if critical_error:
		events.emit(
			events.CRITICAL_ERROR_RECORDED,
			learner=learner,
			competency=doc.competency,
			activity=doc.name,
			reference_doctype="Sparsh Attempt",
			reference_name=attempt.name,
		)

	result = {
		"attempt": attempt.name,
		"outcome": outcome,
		"critical_error": critical_error,
		"hint_level": hint_level,
		"retry_index": retry_index,
	}

	if outcome == "Pass" or critical_error:
		previous_flag = frappe.flags.in_sparsh_runner
		frappe.flags.in_sparsh_runner = True
		try:
			result["evidence"] = _record_evidence(doc, attempt, learner)
		finally:
			frappe.flags.in_sparsh_runner = previous_flag
		result["state"] = frappe.db.get_value(
			"Sparsh Mastery State", {"learner": learner, "competency": doc.competency}, "state"
		)
		if critical_error:
			from sparsh_los.escalation import raise_for_critical_error

			result["escalation"] = raise_for_critical_error(attempt.name, doc.name, learner)
			result["message"] = _(
				"This response is outside safe practice and has been sent to a reviewer."
			)
		return result

	if outcome == "Not Evaluated":
		# A response matching a critical marker still reaches a person, even when the
		# engine may not score it. What it does not do is write critical Evidence,
		# which cannot be cancelled and would be a permanent block imposed on the
		# authority of a rule nobody has validated.
		if _is_critical(doc, response):
			from sparsh_los.escalation import raise_for_critical_error

			result["escalation"] = raise_for_critical_error(attempt.name, doc.name, learner)
			result["message"] = _(
				"Recorded and sent to a reviewer: this response needs a person to look at it."
			)
			return result

		if (doc.evaluation_mode or "") in NOT_SCORED_MODES:
			# Telling the learner a person would review it promised a verdict that
			# never comes: nothing queues a Reflection.
			result["message"] = _("Recorded. A reflection is kept for your record and is not scored.")
			return result

		result["message"] = _("Recorded. This activity is reviewed by a person.")
		return result

	next_level = min(hint_level + 1, MAX_HINT_LEVEL)
	result["hint"] = _hint_for(doc, next_level)
	if result["hint"]:
		events.emit(
			events.HINT_SHOWN,
			learner=learner,
			competency=doc.competency,
			activity=doc.name,
			detail=f"level={next_level}",
		)
	result["hint_level"] = next_level
	result["retry_index"] = retry_index + 1
	result["can_retry"] = next_level < MAX_HINT_LEVEL or bool(result["hint"])
	return result


def _record_evidence(activity, attempt, learner):
	"""Evidence is written by the engine, never by the learner."""
	evidence = frappe.new_doc("Sparsh Evidence")
	evidence.learner = learner
	evidence.competency = activity.competency
	evidence.activity = activity.name
	evidence.activity_version = activity.version
	evidence.attempt = attempt.name
	evidence.outcome = attempt.outcome
	evidence.assistance_level = attempt.hint_level_used
	evidence.critical_error = attempt.critical_error
	evidence.insert(ignore_permissions=True)
	evidence.submit()
	return evidence.name
