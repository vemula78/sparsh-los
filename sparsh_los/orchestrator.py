# Copyright (c) 2026, SSSIHMS and contributors
# For license information, please see license.txt

"""What should this learner encounter next?

The engine does not ask which course to complete next. It asks what experience would
produce the next piece of credible evidence, and that is a different question: the
answer depends on what the learner has already shown, not on where they are in a list.

Selection is deterministic and cheap. Nothing here calls a model.
"""

import frappe
from frappe import _

from sparsh_los.permissions import is_restricted
from sparsh_los.mastery import (
	DEMONSTRATED,
	EXPLORING,
	MASTERED,
	NOT_STARTED,
	PRACTISING,
	derive_state,
	has_blocking_critical_error,
)

# Why a learner is being given this next, in the order the reasons are checked.
REMEDIATION = "remediation"
PREREQUISITE = "prerequisite"
PRACTICE = "practice"
CONSOLIDATION = "consolidation"
PATHWAY_COMPLETE = "pathway complete"


def _prerequisites_unmet(learner, competency):
	"""Prerequisite competencies not yet demonstrated, in declared order."""
	rows = frappe.get_all(
		"Sparsh Competency Prerequisite",
		filters={"parent": competency},
		fields=["prerequisite"],
		order_by="idx asc",
	)

	unmet = []
	for row in rows:
		if derive_state(learner, row.prerequisite) not in (DEMONSTRATED, MASTERED):
			unmet.append(row.prerequisite)

	return unmet


def _activities_for(competency):
	return frappe.get_all(
		"Sparsh Activity",
		filters={"competency": competency},
		fields=["name", "title", "activity_type", "version"],
		order_by="creation asc",
	)


def _passed_activities(learner, competency):
	return {
		row.activity
		for row in frappe.get_all(
			"Sparsh Evidence",
			filters={
				"learner": learner,
				"competency": competency,
				"outcome": "Pass",
				"critical_error": 0,
				"docstatus": 1,
			},
			fields=["activity"],
		)
		if row.activity
	}


@frappe.whitelist()
def next_experience(competency, learner=None):
	"""Return the next activity for this learner, and why.

	The reason matters as much as the activity: a learner sent back to an activity
	after a critical error is not doing the same thing as a learner meeting it for
	the first time, and the record should say so.
	"""
	learner = learner or frappe.session.user

	if learner != frappe.session.user and is_restricted():
		frappe.throw(_("You can only request your own next activity"), frappe.PermissionError)

	unmet = _prerequisites_unmet(learner, competency)
	if unmet:
		return {
			"reason": PREREQUISITE,
			"blocked_by": unmet,
			"activity": None,
			"message": "Earlier competencies are not yet demonstrated.",
		}

	activities = _activities_for(competency)
	if not activities:
		return {"reason": None, "activity": None, "message": "No activities are defined yet."}

	state = derive_state(learner, competency)

	# A standing critical error outranks everything: the learner repeats the work
	# where the unsafe response happened, not whatever comes next in a sequence.
	if has_blocking_critical_error(learner, competency):
		activity = _last_critical_activity(learner, competency) or activities[0]["name"]
		return {
			"reason": REMEDIATION,
			"activity": activity,
			"state": state,
			"message": "An unresolved critical error must be worked through before progressing.",
		}

	if state == MASTERED:
		return {"reason": None, "activity": None, "state": state, "message": "Nothing outstanding."}

	passed = _passed_activities(learner, competency)
	unseen = [a for a in activities if a["name"] not in passed]

	if unseen:
		return {
			"reason": PRACTICE if state in (NOT_STARTED, EXPLORING, PRACTISING) else CONSOLIDATION,
			"activity": unseen[0]["name"],
			"title": unseen[0]["title"],
			"state": state,
		}

	# Everything has been passed at least once but mastery is not established:
	# repeat the earliest, ideally with a variant.
	return {
		"reason": CONSOLIDATION,
		"activity": activities[0]["name"],
		"title": activities[0]["title"],
		"state": state,
	}


def _last_critical_activity(learner, competency):
	rows = frappe.get_all(
		"Sparsh Evidence",
		filters={"learner": learner, "competency": competency, "critical_error": 1, "docstatus": 1},
		fields=["activity"],
		order_by="creation desc",
		limit=1,
	)
	return rows[0].activity if rows else None


def _pathway_steps(pathway):
	return frappe.get_all(
		"Sparsh Pathway Step",
		filters={"parent": pathway},
		fields=["step_order", "activity", "competency", "is_mandatory"],
		order_by="step_order asc, idx asc",
	)


@frappe.whitelist()
def next_in_pathway(pathway, learner=None):
	"""Walk an ordered pathway and return the first step the learner still owes.

	A pathway is a sequence somebody designed; within a step the ordinary rules still
	apply, so a standing critical error sends the learner back even mid-pathway. A
	mandatory step is not passed over, an optional one is.
	"""
	learner = learner or frappe.session.user
	if learner != frappe.session.user and is_restricted():
		frappe.throw(_("You can only request your own pathway position"), frappe.PermissionError)

	steps = _pathway_steps(pathway)
	if not steps:
		return {"reason": None, "activity": None, "message": "This pathway has no steps."}

	for step in steps:
		competency = step.competency or frappe.db.get_value(
			"Sparsh Activity", step.activity, "competency"
		)
		if not competency:
			continue

		# Safety outranks sequence, here as everywhere.
		if has_blocking_critical_error(learner, competency):
			return {
				"reason": REMEDIATION,
				"activity": _last_critical_activity(learner, competency) or step.activity,
				"competency": competency,
				"step": step.step_order,
				"message": "An unresolved critical error must be worked through before progressing.",
			}

		state = derive_state(learner, competency)
		if state in (DEMONSTRATED, MASTERED):
			continue

		if not step.is_mandatory and state != NOT_STARTED:
			# An optional step already attempted does not hold the learner up.
			continue

		suggestion = next_experience(competency, learner)
		if suggestion.get("activity"):
			return dict(suggestion, competency=competency, step=step.step_order, pathway=pathway)

	return {
		"reason": PATHWAY_COMPLETE,
		"activity": None,
		"pathway": pathway,
		"message": "Every step in this pathway has been demonstrated.",
	}
