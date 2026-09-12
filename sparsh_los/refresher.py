# Copyright (c) 2026, SSSIHMS and contributors
# For license information, please see license.txt

"""Refreshers: keeping competence current without repeating basic training.

A certified volunteer should not reopen the whole curriculum. They get one short
piece of work, triggered by one of three things: time since they last demonstrated,
a rule that changed underneath them, or a gap their own performance revealed.

Assignment is a plain function so it can be called and asserted directly. The
scheduler calls the same function; nothing here depends on the scheduler running,
which is what makes it testable.
"""

import frappe

from sparsh_los.mastery import DEMONSTRATED, MASTERED, REFRESH_DUE, derive_state

TIME_ELAPSED = "Time elapsed"
RULE_CHANGED = "Rule changed"
PERFORMANCE_GAP = "Performance gap"


def _already_assigned(learner, competency, reason):
	return frappe.db.exists(
		"Sparsh Refresher Assignment",
		{"learner": learner, "competency": competency, "trigger_reason": reason, "status": "Assigned"},
	)


def assign(learner, competency, reason, detail=None):
	"""Assign one refresher. Idempotent: an open assignment is not duplicated."""
	if _already_assigned(learner, competency, reason):
		return None

	doc = frappe.new_doc("Sparsh Refresher Assignment")
	doc.learner = learner
	doc.competency = competency
	doc.trigger_reason = reason
	doc.status = "Assigned"
	doc.detail = detail
	doc.insert(ignore_permissions=True)
	return doc.name


def evaluate_time_based():
	"""Assign a refresher to everyone whose demonstration has aged out.

	Called by the scheduler daily, and directly by the acceptance harness.
	"""
	assigned = []
	for row in frappe.get_all(
		"Sparsh Mastery State",
		filters={"state": ("in", (DEMONSTRATED, MASTERED, REFRESH_DUE))},
		fields=["learner", "competency"],
	):
		if derive_state(row.learner, row.competency) != REFRESH_DUE:
			continue

		name = assign(
			row.learner,
			row.competency,
			TIME_ELAPSED,
			"Last demonstration is older than the competency's refresh interval.",
		)
		if name:
			assigned.append(name)

	return assigned


def on_rule_superseded(rule):
	"""A rule changed, so everyone judged against it needs to see the new one.

	This is the loop the Master Build Guide asks for: a source-of-truth change does
	not quietly invalidate past assessments, it schedules the people affected to
	meet the new rule.
	"""
	competencies = frappe.get_all(
		"Sparsh Competency Rule Link", filters={"rule": rule}, fields=["parent"], pluck="parent"
	)
	if not competencies:
		return []

	assigned = []
	for competency in set(competencies):
		for row in frappe.get_all(
			"Sparsh Mastery State",
			filters={"competency": competency, "state": ("in", (DEMONSTRATED, MASTERED))},
			fields=["learner"],
		):
			name = assign(
				row.learner,
				competency,
				RULE_CHANGED,
				f"Rule {rule} was superseded after this competency was demonstrated.",
			)
			if name:
				assigned.append(name)

	return assigned


def daily():
	"""Scheduler entry point."""
	evaluate_time_based()
