# Copyright (c) 2026, SSSIHMS and contributors
# For license information, please see license.txt

"""The refresher pathway's front door.

Section 8.2 of the build guide is explicit that a currently-certified volunteer must not
be put back through the whole new-volunteer pathway: "Begin with a short competency
diagnostic", then "assign only weak-area refreshers". Neither existed. `PERFORMANCE_GAP`
was declared in `refresher.py` and assigned by nothing, and the orchestrator handed a
refresher learner `activities[0]` regardless of what they were actually weak at -- so a
volunteer sent back for one competency met whichever activity happened to sort first.

A diagnostic here is one activity per competency and nothing more. It is deliberately not
a second certification: it exists to find out where to look.

**What counts as a gap is not a clinical judgement and no number is invented for it.**
A competency is weak when the learner's diagnostic attempt was not an unaided pass --
which is the assistance model the engine already runs on, not a threshold somebody would
have to approve. Section 25 item 5 permits placeholder non-clinical logic; this does not
even need the placeholder.
"""

import frappe
from frappe import _

from sparsh_los.permissions import is_restricted, require_enrolment, require_reviewer

DIAGNOSTIC = "Diagnostic"


def _competencies_with_activities():
	rows = frappe.get_all(
		"Sparsh Activity", fields=["competency", "name", "activity_id"], order_by="activity_id asc"
	)
	first = {}
	for row in rows:
		if row.competency and row.competency not in first:
			first[row.competency] = row
	return first


@frappe.whitelist()
def start(learner=None):
	"""Open a diagnostic for a learner, one activity per competency.

	Returns the activities to attempt. It records the checkpoint rather than only
	handing back a list, because §7 asks for an Assessment Event and because closing
	the diagnostic later has to know which one it is closing.
	"""
	learner = learner or frappe.session.user
	require_enrolment()
	if learner != frappe.session.user and is_restricted():
		frappe.throw(_("You can only start your own diagnostic"), frappe.PermissionError)

	open_event = frappe.db.get_value(
		"Sparsh Assessment Event",
		{"learner": learner, "purpose": DIAGNOSTIC, "status": "Open"},
		"name",
	)
	if open_event:
		# Two open diagnostics for one learner makes "close the diagnostic" ambiguous,
		# and the second would reassign every refresher the first already raised.
		return {"event": open_event, "already_open": True,
				"activities": _diagnostic_activities(open_event)}

	first = _competencies_with_activities()
	if not first:
		frappe.throw(_("No competency has an activity, so there is nothing to diagnose"))

	event = frappe.new_doc("Sparsh Assessment Event")
	event.learner = learner
	event.purpose = DIAGNOSTIC
	event.status = "Open"
	event.detail = "One activity per competency, to find where refreshers are needed."
	event.insert(ignore_permissions=True)

	from sparsh_los import events

	events.emit(
		events.ASSESSMENT_STARTED,
		learner=learner,
		detail=f"purpose={DIAGNOSTIC} competencies={len(first)}",
		reference_doctype=event.doctype,
		reference_name=event.name,
	)
	frappe.db.commit()

	return {
		"event": event.name,
		"already_open": False,
		"activities": [
			{"competency": competency, "activity": row.name, "activity_id": row.activity_id}
			for competency, row in sorted(first.items())
		],
	}


def _diagnostic_activities(event):
	learner = frappe.db.get_value("Sparsh Assessment Event", event, "learner")
	first = _competencies_with_activities()
	return [
		{"competency": competency, "activity": row.name, "activity_id": row.activity_id,
		 "attempted": bool(_latest_attempt(learner, row.name, event))}
		for competency, row in sorted(first.items())
	]


def _latest_attempt(learner, activity, event):
	"""The learner's most recent attempt on this activity since the diagnostic opened."""
	since = frappe.db.get_value("Sparsh Assessment Event", event, "started_at")
	rows = frappe.get_all(
		"Sparsh Attempt",
		filters={"learner": learner, "activity": activity, "creation": (">=", since)},
		fields=["name", "outcome", "hint_level_used"],
		order_by="creation desc",
		limit=1,
	)
	return rows[0] if rows else None


@frappe.whitelist()
def close(event):
	"""Close a diagnostic and assign a refresher for each weak competency.

	A reviewer action: it writes refresher assignments, which hold a learner's
	competency at Refresh Due and suspend the certificate that depended on it.
	"""
	require_reviewer()

	doc = frappe.get_doc("Sparsh Assessment Event", event)
	if doc.purpose != DIAGNOSTIC:
		frappe.throw(_("That assessment event is not a diagnostic"))
	if doc.status == "Completed":
		frappe.throw(_("That diagnostic is already closed"))

	from sparsh_los import refresher

	strong, weak, not_attempted, assigned = [], [], [], []
	for row in _competencies_with_activities().items():
		competency, activity = row
		attempt = _latest_attempt(doc.learner, activity.name, doc.name)
		if not attempt:
			not_attempted.append(competency)
			continue

		# Weak means "not an unaided pass". No invented threshold: this is the same
		# assistance model every other judgement in the engine runs on.
		if attempt.outcome == "Pass" and not (attempt.hint_level_used or 0):
			strong.append(competency)
			continue

		weak.append(competency)
		name = refresher.assign(
			doc.learner,
			competency,
			refresher.PERFORMANCE_GAP,
			detail=(
				f"Diagnostic on {activity.activity_id} was not an unaided pass "
				f"(outcome {attempt.outcome}, assistance {attempt.hint_level_used or 0})."
			),
			focus_activity=activity.name,
		)
		if name:
			assigned.append(name)

	doc.status = "Completed"
	doc.outcome_summary = (
		f"{len(strong)} competency(ies) demonstrated unaided, {len(weak)} needing a "
		f"refresher, {len(not_attempted)} not attempted."
	)
	doc.save(ignore_permissions=True)

	from sparsh_los import events

	events.emit(
		events.ASSESSMENT_COMPLETED,
		learner=doc.learner,
		detail=f"purpose={DIAGNOSTIC} weak={len(weak)} strong={len(strong)}",
		reference_doctype=doc.doctype,
		reference_name=doc.name,
	)
	frappe.db.commit()

	return {
		"event": doc.name,
		"strong": sorted(strong),
		"weak": sorted(weak),
		# Reported, never silently treated as strong: a competency the learner did not
		# attempt is unknown, and calling it demonstrated would be the "Optimistic Path".
		"not_attempted": sorted(not_attempted),
		"refreshers_assigned": assigned,
	}
