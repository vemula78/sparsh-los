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

from sparsh_los.mastery import DEMONSTRATED, MASTERED, REFRESH_DUE, derive_state, recompute_mastery

TIME_ELAPSED = "Time elapsed"
RULE_CHANGED = "Rule changed"
RESOURCE_CHANGED = "Resource changed"
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

		# Persist the regression: without this the stored state and any standing
		# certification stayed as they were, so a certificate outlived its currency.
		recompute_mastery(row.learner, row.competency)

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

	# Who was actually judged under this rule. Attempts record the rule they were
	# evaluated against, so the refresher can go to the people it concerns rather than
	# to everyone who happens to hold the competency.
	judged_under = {
		row.learner
		for row in frappe.get_all(
			"Sparsh Attempt", filters={"rule": rule}, fields=["learner"]
		)
	}

	assigned = []
	for competency in set(competencies):
		for row in frappe.get_all(
			"Sparsh Mastery State",
			filters={"competency": competency, "state": ("in", (DEMONSTRATED, MASTERED))},
			fields=["learner"],
		):
			# Nobody judged under the rule means we cannot tell who it affected, so
			# everyone holding the competency is scheduled rather than nobody.
			if judged_under and row.learner not in judged_under:
				continue

			name = assign(
				row.learner,
				competency,
				RULE_CHANGED,
				f"Rule {rule} was superseded after this competency was demonstrated.",
			)
			# The recompute that follows an assignment lives in the Refresher
			# Assignment controller, so every trigger gets it, not just this one.
			if name:
				assigned.append(name)

	return assigned


def close_satisfied(learner, competency):
	"""Close refreshers the learner has already answered with fresh evidence.

	Without this the engine could assign a refresher but never finish one: nothing
	outside the desk ever set a row to Completed, so Refresh Due -- and the suspended
	certificate that comes with it -- lasted until a human edited the row by hand. A
	refresher is short work; an indefinite suspension is not what it is for.

	Satisfied means an independent pass recorded *after* the assignment was made.
	Evidence the learner already had cannot answer a refresher, or superseded content
	would close its own refresher the moment it was assigned.

	Written with db.set_value rather than a save on purpose: the controller's on_update
	recomputes mastery, and this runs from inside recompute_mastery. The event is
	emitted here instead, so closing this way still shows up in the log.
	"""
	from sparsh_los import events
	from sparsh_los.mastery import _independent_passes, _submitted_evidence

	rows = frappe.get_all(
		"Sparsh Refresher Assignment",
		filters={"learner": learner, "competency": competency, "status": "Assigned"},
		fields=["name", "assigned_on"],
	)
	if not rows:
		return []

	passes = _independent_passes(_submitted_evidence(learner, competency))
	closed = []
	for row in rows:
		if not row.assigned_on:
			continue
		fresh = [
			p
			for p in passes
			if frappe.utils.get_datetime(p.recorded_at or p.creation)
			> frappe.utils.get_datetime(row.assigned_on)
		]
		if not fresh:
			continue

		frappe.db.set_value(
			"Sparsh Refresher Assignment",
			row.name,
			{"status": "Completed", "completed_on": frappe.utils.now_datetime()},
		)
		events.emit(
			events.REFRESHER_COMPLETED,
			learner=learner,
			competency=competency,
			detail="closed by fresh evidence",
			reference_doctype="Sparsh Refresher Assignment",
			reference_name=row.name,
		)
		closed.append(row.name)

	return closed


def on_resource_superseded(resource):
	"""A learning resource was replaced, so everyone who relied on it should see the new one.

	Unlike a rule, a resource is not stamped on an Attempt — nothing records which
	reading a learner actually did. So this cannot narrow to the people it concerns the
	way `on_rule_superseded` can, and it schedules everyone currently holding the
	competency instead. Scheduling somebody who did not need it costs them one short
	piece of work; missing somebody who did leaves them certified against withdrawn
	material.
	"""
	competencies = frappe.get_all(
		"Sparsh Competency Resource Link",
		filters={"resource": resource},
		fields=["parent"],
		pluck="parent",
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
				RESOURCE_CHANGED,
				f"Learning resource {resource} was superseded after this competency was demonstrated.",
			)
			if name:
				assigned.append(name)

	return assigned


def daily():
	"""Scheduler entry point."""
	evaluate_time_based()
