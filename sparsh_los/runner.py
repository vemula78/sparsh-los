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
AUTO_SCORED_MODES = ("Deterministic",)
NOT_SCORED_MODES = ("Reflection",)
AWAITING_IMPLEMENTATION_MODES = ("Numeric validation", "Rubric", "AI-assisted")


def evaluate(activity, response):
	"""Return (outcome, critical_error). Deterministic; no model call.

	Every mode except Deterministic returns Not Evaluated and waits for a reviewer.
	That is the right answer for judgement-heavy work, and for the modes not yet
	built it is the only safe one: the alternative is falling through to the
	string comparison, which would score a rubric activity as though it were a
	multiple-choice question.
	"""
	# Critical markers are checked whatever the mode. An unsafe response is unsafe
	# whether or not the engine can grade the rest of the answer.
	if _is_critical(activity, response):
		return "Fail", 1

	mode = activity.evaluation_mode or "Human review"
	if mode not in AUTO_SCORED_MODES:
		return "Not Evaluated", 0

	# The rule behind the activity must be validated before the engine scores against
	# it. This was a convention written in the docs and enforced nowhere: an activity
	# set Deterministic against a Draft safety-critical rule auto-scored, wrote
	# Evidence and moved Mastery -- which is exactly "an unvalidated rule as production
	# logic", the one thing the programme owner ruled out.
	#
	# An activity with no rule linked is not scoring against a clinical rule at all
	# (the non-clinical cross-domain competency is the case that matters), so it is
	# left alone. A rule that exists but is not Validated stops the scoring.
	rule = _governing_rule(activity.competency)
	if rule and frappe.db.get_value("Sparsh Source of Truth Rule", rule, "status") != "Validated":
		return "Not Evaluated", 0

	accepted = _accepted_responses(activity)
	if not accepted:
		# Nothing to compare against is a content gap, not a learner failure.
		return "Not Evaluated", 0

	return ("Pass", 0) if _normalise(response) in accepted else ("Fail", 0)


@frappe.whitelist()
def start(activity):
	"""Open a session on one activity. Returns the instruction and nothing more."""
	_require_enrolment()
	doc = _activity(activity)
	events.emit(
		events.ACTIVITY_STARTED,
		learner=frappe.session.user,
		competency=doc.competency,
		activity=doc.name,
	)
	return {
		"activity": doc.name,
		"title": doc.title,
		"instruction": doc.instruction,
		"competency": doc.competency,
		"hint_level": 0,
		"retry_index": 0,
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
		fields=["outcome", "hint_level_used"],
		order_by="creation asc",
	)

	failures = sum(1 for row in attempts if row.outcome not in ("Pass", "Not Evaluated"))
	seen = max((row.hint_level_used or 0) for row in attempts) if attempts else 0

	return min(max(failures, seen), MAX_HINT_LEVEL), len(attempts)


@frappe.whitelist()
def submit(activity, response):
	"""Record one response, evaluate it, and return the minimum help required.

	A pass produces Evidence, which recomputes mastery. A failure returns the next
	rung of the hint ladder and invites a retry; the answer is revealed only at the
	top of the ladder.
	"""
	doc = _activity(activity)
	learner = frappe.session.user

	_require_enrolment()

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
