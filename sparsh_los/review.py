# Copyright (c) 2026, SSSIHMS and contributors
# For license information, please see license.txt

"""Turning a reviewed attempt into evidence.

Some competencies cannot be scored by matching text — a coaching conversation, an
observed performance, a piece of written reasoning. For those the runner records
what the learner did and stops, and a person decides what it was worth.

This is the path from that recorded attempt to Evidence. It is deliberately the only
way a person creates Evidence, so the reconciliation rules on Evidence apply to a
reviewer's judgement exactly as they apply to the engine's.
"""

import frappe
from frappe import _

from sparsh_los import events
from sparsh_los.permissions import require_reviewer
from sparsh_los.runner import MAX_HINT_LEVEL, NOT_SCORED_MODES, RUBRIC_MODE, _hint_for, _lines

REVIEWABLE_OUTCOMES = ("Pass", "Partial", "Fail")


def _require_reviewer():
	require_reviewer()


# A reviewer queue nobody can read is not a queue; this is a sanity bound, not a quota.
MAX_QUEUE = 500


@frappe.whitelist()
def pending(competency=None, limit=50):
	"""Attempts waiting on a person: recorded, evaluated by nobody, no evidence yet."""
	_require_reviewer()

	# `limit` is caller input on a whitelisted endpoint. Unbounded it scans the whole
	# table; negative it means whatever the database decides; malformed it raised a bare
	# ValueError the caller saw as an internal error rather than a rejected argument.
	try:
		limit = int(limit)
	except (TypeError, ValueError):
		frappe.throw(_("A queue limit must be a whole number"))
	if limit < 1 or limit > MAX_QUEUE:
		frappe.throw(_("A queue limit must be between 1 and {0}").format(MAX_QUEUE))

	# The limit is applied to rows that are already eligible. It used to take the oldest
	# `limit` attempts and then discard those with evidence or from another competency,
	# so fifty old reviewed attempts returned an empty queue while unreviewed work sat
	# behind them -- the queue starved silently rather than reporting truncation.
	#
	# A Reflection is stored, not judged: it shares the `Not Evaluated` outcome with
	# work that genuinely waits on a person, so it is excluded here by the activity's
	# mode rather than by outcome. Without this every reflection sat in the queue and,
	# at pilot scale, buried the attempts that needed a verdict. The exclusion reads the
	# activity's *current* mode -- the attempt does not record the mode it was made
	# under -- so re-authoring an activity into or out of Reflection moves its old
	# attempts with it.
	# `validation_required` and `source_status` travel with the row on purpose. Every
	# seeded case is "Needs review", and the reviewer is the last person who can notice
	# that the rule behind the case they are judging has not been approved. Leaving it
	# in the DocType and not in the queue means it is visible only to somebody who
	# thinks to go and look.
	waiting = frappe.db.sql(
		"""
		select a.name, a.learner, a.activity, a.response, a.hint_level_used, a.attempted_at,
		       act.competency, act.title, act.evaluation_mode, act.rubric_criteria,
		       act.validation_required, act.source_status
		from `tabSparsh Attempt` a
		inner join `tabSparsh Activity` act on act.name = a.activity
		where a.outcome = 'Not Evaluated'
		  and not exists (
		      select 1 from `tabSparsh Evidence` e where e.attempt = a.name
		  )
		  and ifnull(act.evaluation_mode, '') not in %(not_scored)s
		  and (%(competency)s is null or act.competency = %(competency)s)
		order by a.creation asc
		limit %(limit)s
		""",
		{"competency": competency or None, "limit": limit, "not_scored": NOT_SCORED_MODES},
		as_dict=True,
	)

	return waiting


def _attempt_for_review(attempt):
	"""The attempt a reviewer may judge, or a refusal.

	Who is acting is `frappe.session.user`, never anything in the payload. The
	self-review refusal is a permission error, not a validation error: the caller
	is not allowed to do this, whatever they send.
	"""
	_require_reviewer()

	doc = frappe.get_doc("Sparsh Attempt", attempt)
	if doc.learner == frappe.session.user:
		frappe.throw(_("You cannot record evidence for your own attempt"), frappe.PermissionError)

	if frappe.db.exists("Sparsh Evidence", {"attempt": doc.name}):
		frappe.throw(_("This attempt has already been turned into evidence"))

	activity = frappe.get_doc("Sparsh Activity", doc.activity)

	# Keeping a Reflection out of `pending` is not enough: these endpoints take an
	# attempt by name, so a reviewer could still turn one into Evidence and move
	# mastery on the strength of a learner's private reflection.
	if (activity.evaluation_mode or "") in NOT_SCORED_MODES:
		frappe.throw(_("A reflection is stored for the learner's record and is not turned into evidence"))

	return doc, activity


def _write_verdict(doc, activity, outcome, assistance_level, critical_error, comments):
	"""One Evidence record from a reviewer's verdict, then the hint that verdict earns.

	On Partial or Fail the next rung of the activity's ladder comes back with the
	result. This is how free-text work gets a graded hint and a retry: the runner
	could not return one at submit time because it had no verdict, and the
	assistance it implies is counted into the learner's next attempt by
	`runner._session_position`, which reads these verdicts. The hint itself reaches
	the learner through `runner.start`, which returns the strongest rung already
	issued.
	"""
	evidence = frappe.new_doc("Sparsh Evidence")
	evidence.learner = doc.learner
	evidence.competency = activity.competency
	evidence.activity = doc.activity
	evidence.activity_version = activity.version
	evidence.attempt = doc.name
	evidence.outcome = outcome
	evidence.assistance_level = int(assistance_level)
	evidence.critical_error = critical_error
	evidence.ai_feedback_summary = comments
	evidence.human_review_status = "Approved"
	previous_flag = frappe.flags.in_sparsh_runner
	frappe.flags.in_sparsh_runner = True
	try:
		evidence.insert(ignore_permissions=True)
		evidence.submit()
	finally:
		frappe.flags.in_sparsh_runner = previous_flag

	# The attempt is not rewritten: it records what the learner did, and a reviewer's
	# verdict is a separate fact. "Has this been judged?" is answered by whether
	# Evidence cites the attempt, which is how pending() already asks it.

	result = {
		"evidence": evidence.name,
		"attempt": doc.name,
		"outcome": outcome,
		"state": frappe.db.get_value(
			"Sparsh Mastery State", {"learner": doc.learner, "competency": activity.competency}, "state"
		),
	}

	if outcome in ("Partial", "Fail") and not critical_error:
		next_level = min((doc.hint_level_used or 0) + 1, MAX_HINT_LEVEL)
		result["hint"] = _hint_for(activity, next_level)
		result["hint_level"] = next_level
		# A retry is always open after a person's verdict; whether the ladder has
		# another rung to offer is a separate fact, reported as `hint`.
		result["can_retry"] = True
		if result["hint"]:
			events.emit(
				events.HINT_SHOWN,
				learner=doc.learner,
				competency=activity.competency,
				activity=doc.activity,
				detail=f"level={next_level} via=review",
			)

	return result


@frappe.whitelist()
def record_evidence(attempt, outcome, assistance_level=None, critical_error=0, comments=None):
	"""A reviewer decides what a recorded attempt was worth.

	Assistance defaults to what the attempt actually recorded rather than to zero: a
	reviewer should have to state deliberately that a learner worked unaided.
	"""
	if outcome not in REVIEWABLE_OUTCOMES:
		frappe.throw(_("{0} is not a reviewable outcome").format(outcome))

	doc, activity = _attempt_for_review(attempt)

	critical_error = int(critical_error or 0) or int(doc.critical_error or 0)
	if assistance_level is None:
		assistance_level = doc.hint_level_used or 0

	return _write_verdict(doc, activity, outcome, assistance_level, critical_error, comments)


def aggregate_rubric(total, met):
	"""Pass when every criterion is met, Fail when none is, Partial between.

	Deterministic and strict on purpose. Any threshold below "all" says which
	criterion a learner may miss and still pass -- a judgement about the content,
	which belongs to the activity's author and the programme owner, not to the
	engine. If one is ever wanted it is a permlevel-1 field beside the criteria, and
	this function is where it would be read.
	"""
	if total and met == total:
		return "Pass"
	if met == 0:
		return "Fail"
	return "Partial"


@frappe.whitelist()
def score_rubric(attempt, criteria_met, comments=None):
	"""A reviewer says which rubric criteria were met; the engine decides what that is worth.

	`criteria_met` is a list of 1-based line numbers into the activity's
	`rubric_criteria` -- line 1 is criterion 1, the same shape as the hint ladder.
	The reviewer never sends an outcome and never sends an assistance level: the
	outcome is aggregated here and assistance is what the attempt recorded, so a
	rubric verdict can neither inflate a result nor claim the work was less assisted
	than it was.

	The criteria are permlevel-1 text on the Activity, not a child table, for the
	same reason the hint ladder is: a child table is a separately queryable DocType,
	and the person being assessed could read the answer key through it.
	"""
	doc, activity = _attempt_for_review(attempt)

	if (activity.evaluation_mode or "") != RUBRIC_MODE:
		frappe.throw(
			_("{0} is a {1} activity; a rubric verdict applies only to a Rubric activity").format(
				activity.name, activity.evaluation_mode or "blank-mode"
			)
		)

	criteria = _lines(activity.rubric_criteria)
	if not criteria:
		# A content gap, not a verdict: nothing was authored to judge against.
		frappe.throw(_("{0} has no rubric criteria to score against").format(activity.name))

	if isinstance(criteria_met, str):
		criteria_met = frappe.parse_json(criteria_met)
	if criteria_met is None:
		criteria_met = []
	if not isinstance(criteria_met, (list, tuple)):
		frappe.throw(_("criteria_met must be a list of criterion numbers"))

	met = set()
	for item in criteria_met:
		try:
			index = int(item)
		except (TypeError, ValueError):
			frappe.throw(_("{0} is not a criterion number").format(item))
		if index < 1 or index > len(criteria):
			frappe.throw(
				_("Criterion {0} does not exist; this activity has {1}").format(index, len(criteria))
			)
		met.add(index)

	outcome = aggregate_rubric(len(criteria), len(met))

	# Indices only, never the criteria text: the learner can read their own Evidence.
	summary = _("Rubric: {0} of {1} criteria met").format(len(met), len(criteria))
	if met:
		summary += " (" + ", ".join(str(i) for i in sorted(met)) + ")"
	if comments:
		summary += "\n" + comments

	# A rubric verdict does not decide safety; the attempt's own flag is inherited,
	# exactly as `record_evidence` inherits it. A reviewer who sees a scope breach
	# the markers missed records it through `record_evidence` with critical_error=1.
	critical_error = int(doc.critical_error or 0)

	result = _write_verdict(doc, activity, outcome, doc.hint_level_used or 0, critical_error, summary)
	result["criteria_met"] = sorted(met)
	result["criteria_total"] = len(criteria)
	return result
