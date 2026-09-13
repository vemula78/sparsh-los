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

from sparsh_los.permissions import require_reviewer
from sparsh_los.runner import NOT_SCORED_MODES

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
	waiting = frappe.db.sql(
		"""
		select a.name, a.learner, a.activity, a.response, a.hint_level_used, a.attempted_at,
		       act.competency, act.title
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


@frappe.whitelist()
def record_evidence(attempt, outcome, assistance_level=None, critical_error=0, comments=None):
	"""A reviewer decides what a recorded attempt was worth.

	Assistance defaults to what the attempt actually recorded rather than to zero: a
	reviewer should have to state deliberately that a learner worked unaided.
	"""
	_require_reviewer()

	if outcome not in REVIEWABLE_OUTCOMES:
		frappe.throw(_("{0} is not a reviewable outcome").format(outcome))

	doc = frappe.get_doc("Sparsh Attempt", attempt)
	if doc.learner == frappe.session.user:
		frappe.throw(_("You cannot record evidence for your own attempt"), frappe.PermissionError)

	if frappe.db.exists("Sparsh Evidence", {"attempt": doc.name}):
		frappe.throw(_("This attempt has already been turned into evidence"))

	competency = frappe.db.get_value("Sparsh Activity", doc.activity, "competency")
	version = frappe.db.get_value("Sparsh Activity", doc.activity, "version")

	# Keeping a Reflection out of `pending` is not enough: this endpoint takes an
	# attempt by name, so a reviewer could still turn one into Evidence and move
	# mastery on the strength of a learner's private reflection.
	if (frappe.db.get_value("Sparsh Activity", doc.activity, "evaluation_mode") or "") in NOT_SCORED_MODES:
		frappe.throw(_("A reflection is stored for the learner's record and is not turned into evidence"))

	critical_error = int(critical_error or 0) or int(doc.critical_error or 0)
	if assistance_level is None:
		assistance_level = doc.hint_level_used or 0

	evidence = frappe.new_doc("Sparsh Evidence")
	evidence.learner = doc.learner
	evidence.competency = competency
	evidence.activity = doc.activity
	evidence.activity_version = version
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

	return {
		"evidence": evidence.name,
		"attempt": doc.name,
		"state": frappe.db.get_value(
			"Sparsh Mastery State", {"learner": doc.learner, "competency": competency}, "state"
		),
	}
