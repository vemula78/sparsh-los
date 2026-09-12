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

from sparsh_los.permissions import is_restricted

REVIEWABLE_OUTCOMES = ("Pass", "Partial", "Fail")


def _require_reviewer():
	if is_restricted():
		frappe.throw(_("Recording evidence is a reviewer action"), frappe.PermissionError)


@frappe.whitelist()
def pending(competency=None, limit=50):
	"""Attempts waiting on a person: recorded, evaluated by nobody, no evidence yet."""
	_require_reviewer()

	attempts = frappe.get_all(
		"Sparsh Attempt",
		filters={"outcome": "Not Evaluated"},
		fields=["name", "learner", "activity", "response", "hint_level_used", "attempted_at"],
		order_by="creation asc",
		limit=int(limit),
	)

	waiting = []
	for attempt in attempts:
		if frappe.db.exists("Sparsh Evidence", {"attempt": attempt.name}):
			continue

		activity = frappe.db.get_value(
			"Sparsh Activity", attempt.activity, ["competency", "title", "version"], as_dict=True
		)
		if not activity:
			continue
		if competency and activity.competency != competency:
			continue

		waiting.append(dict(attempt, competency=activity.competency, title=activity.title))

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
	if frappe.db.exists("Sparsh Evidence", {"attempt": doc.name}):
		frappe.throw(_("This attempt has already been turned into evidence"))

	competency = frappe.db.get_value("Sparsh Activity", doc.activity, "competency")
	version = frappe.db.get_value("Sparsh Activity", doc.activity, "version")

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
	evidence.insert(ignore_permissions=True)
	evidence.submit()

	# The attempt said "nobody has judged this yet". Somebody has now.
	frappe.db.set_value("Sparsh Attempt", doc.name, "outcome", outcome)

	return {
		"evidence": evidence.name,
		"attempt": doc.name,
		"state": frappe.db.get_value(
			"Sparsh Mastery State", {"learner": doc.learner, "competency": competency}, "state"
		),
	}
