# Copyright (c) 2026, SSSIHMS and contributors
# For license information, please see license.txt

"""Read models for the learner and supervisor views.

The supervisor view answers four questions and no more: who is ready to progress,
who is stuck and in which competency, where critical safety errors are occurring,
and what recurring questions should trigger a content or programme change.

Everything here is derived at read time from Evidence and Mastery State. Nothing is
stored, so nothing can drift from the evidence it claims to summarise.
"""

import frappe
from frappe import _

from sparsh_los.mastery import DEMONSTRATED, MASTERED, PRACTISING
from sparsh_los.permissions import is_restricted, require_enrolment, require_reviewer

CERTIFIABLE = (DEMONSTRATED, MASTERED)


@frappe.whitelist()
def learner_view(learner=None):
	"""One learner's position: what they have shown, and what is waiting for them."""
	learner = learner or frappe.session.user
	require_enrolment()

	# Every @frappe.whitelist() method is callable by any logged-in user, so an
	# argument naming a learner is an access decision, not a convenience.
	if learner != frappe.session.user and is_restricted():
		frappe.throw(_("You can only view your own record"), frappe.PermissionError)

	states = frappe.get_all(
		"Sparsh Mastery State",
		filters={"learner": learner},
		fields=["competency", "state", "last_demonstrated", "evidence_count"],
		order_by="competency asc",
	)

	return {
		"learner": learner,
		"competencies": states,
		"demonstrated": [s.competency for s in states if s.state in CERTIFIABLE],
		"open_escalations": frappe.get_all(
			"Sparsh Escalation Question",
			filters={"learner": learner, "status": ("in", ("Open", "Routed to Human"))},
			fields=["name", "question_text", "status", "raised_at"],
			order_by="creation asc",
		),
		"refreshers": frappe.get_all(
			"Sparsh Refresher Assignment",
			filters={"learner": learner, "status": "Assigned"},
			fields=["name", "competency", "trigger_reason", "detail", "assigned_on"],
			order_by="creation asc",
		),
		"certifications": frappe.get_all(
			"Sparsh Certification Record",
			filters={"learner": learner, "docstatus": 1},
			# certification_state matters more than status here: a suspended Full
			# certificate is not a valid one, and hiding that misleads the learner.
			fields=["competency", "certification_status", "certification_state", "certified_on"],
		),
	}


def _require_supervisor():
	"""Cohort-wide views are for people who supervise a cohort."""
	require_reviewer()


def _cohort_states(competency=None):
	filters = {}
	if competency:
		filters["competency"] = competency

	return frappe.get_all(
		"Sparsh Mastery State",
		filters=filters,
		fields=["learner", "competency", "state", "last_demonstrated", "evidence_count"],
	)


@frappe.whitelist()
def supervisor_view(competency=None):
	"""The four questions, answered from evidence."""
	_require_supervisor()
	states = _cohort_states(competency)

	# Who is ready to progress.
	ready = [s for s in states if s.state in CERTIFIABLE]

	# Who is stuck, and why. A supervisor asked "who is stuck" needs to know what to
	# do about it, so each row carries the reason rather than a threshold nobody can
	# see. Three attempts at Practising is deliberately low: catching someone early is
	# the point.
	stuck = []
	for row in states:
		if row.state != PRACTISING or (row.evidence_count or 0) < 3:
			continue

		assisted = frappe.db.count(
			"Sparsh Evidence",
			{
				"learner": row.learner,
				"competency": row.competency,
				"docstatus": 1,
				"outcome": "Pass",
				"assistance_level": (">", 0),
			},
		)
		failures = frappe.db.count(
			"Sparsh Evidence",
			{
				"learner": row.learner,
				"competency": row.competency,
				"docstatus": 1,
				"outcome": "Fail",
			},
		)
		reason = (
			"Passing only with help"
			if assisted and not failures
			else "Repeated failures"
			if failures and not assisted
			else "Mixed results without an unaided pass"
		)
		stuck.append(dict(row, reason=reason, assisted_passes=assisted, failures=failures))

	# Where critical safety errors are occurring.
	critical = frappe.get_all(
		"Sparsh Evidence",
		filters={"critical_error": 1, "docstatus": 1},
		fields=["learner", "competency", "activity", "recorded_at"],
		order_by="creation desc",
		limit=50,
	)

	# What recurring questions should trigger a content or programme change.
	programme_signals = frappe.get_all(
		"Sparsh Escalation Question",
		filters={"disposition": ("in", ("Source-of-truth update", "Curriculum change"))},
		fields=["name", "activity", "escalation_reason", "disposition", "question_text"],
		order_by="creation desc",
	)

	distribution = {}
	for row in states:
		distribution[row.state] = distribution.get(row.state, 0) + 1

	return {
		"learners": len({s.learner for s in states}),
		"state_distribution": distribution,
		"ready_to_progress": ready,
		"stuck": stuck,
		"critical_errors": critical,
		"programme_signals": programme_signals,
		"pending_escalations": frappe.db.count(
			"Sparsh Escalation Question", {"status": ("in", ("Open", "Routed to Human"))}
		),
	}


@frappe.whitelist()
def competency_heatmap():
	"""Learners down, competencies across, mastery state in each cell.

	A cell holds a state, never a percentage: the supervisor should click through to
	the evidence, not read a score.
	"""
	_require_supervisor()
	states = _cohort_states()
	competencies = sorted({s.competency for s in states})

	grid = {}
	for row in states:
		grid.setdefault(row.learner, {})[row.competency] = row.state

	return {"competencies": competencies, "rows": grid}


@frappe.whitelist()
def programme_summary(days=30):
	"""The paragraph a programme manager should be able to read at a glance.

	Section 18 gives the shape: how many are enrolled, how many are active, how many
	are ready, how many need remediation, how many await a person, and what the most
	common gap is. Every figure is counted from evidence at read time, so the summary
	cannot drift from what it claims to summarise.
	"""
	_require_supervisor()

	days = int(days)
	since = frappe.utils.add_days(frappe.utils.now_datetime(), -days)

	learners = {
		row.name
		for row in frappe.get_all(
			"Has Role", filters={"role": "Sparsh Learner", "parenttype": "User"}, fields=["parent as name"]
		)
	}

	active = {
		row.learner
		for row in frappe.get_all(
			"Sparsh Attempt", filters={"creation": (">", since)}, fields=["learner"]
		)
	}

	states = _cohort_states()
	ready, remediation = set(), set()
	for row in states:
		if row.state in CERTIFIABLE:
			ready.add(row.learner)
		elif row.state == PRACTISING:
			remediation.add(row.learner)

	awaiting_person = frappe.db.count("Sparsh Attempt", {"outcome": "Not Evaluated"})
	open_questions = frappe.db.count(
		"Sparsh Escalation Question", {"status": ("in", ("Open", "Routed to Human"))}
	)

	# The most common gap: the competency where the most learners are stuck short of
	# demonstrating it. Named, because "most common gap" is what changes a curriculum.
	gaps = {}
	for row in states:
		if row.state not in CERTIFIABLE:
			gaps[row.competency] = gaps.get(row.competency, 0) + 1
	most_common_gap = max(gaps.items(), key=lambda kv: kv[1])[0] if gaps else None

	certified = {
		row.learner
		for row in frappe.get_all(
			"Sparsh Certification Record",
			filters={"docstatus": 1, "certification_state": "Active"},
			fields=["learner"],
		)
	}

	return {
		"period_days": days,
		"enrolled": len(learners),
		"active_in_period": len(active & learners) if learners else len(active),
		"certification_ready": len(ready),
		"require_remediation": len(remediation),
		"attempts_awaiting_a_person": awaiting_person,
		"open_questions": open_questions,
		"certified": len(certified),
		"most_common_gap": most_common_gap,
		"gaps": dict(sorted(gaps.items(), key=lambda kv: -kv[1])),
	}
