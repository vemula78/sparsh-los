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
from sparsh_los.permissions import is_restricted

CERTIFIABLE = (DEMONSTRATED, MASTERED)


@frappe.whitelist()
def learner_view(learner=None):
	"""One learner's position: what they have shown, and what is waiting for them."""
	learner = learner or frappe.session.user

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
		"certifications": frappe.get_all(
			"Sparsh Certification Record",
			filters={"learner": learner, "docstatus": 1},
			fields=["competency", "certification_status", "certified_on"],
		),
	}


def _require_supervisor():
	"""Cohort-wide views are for people who supervise a cohort."""
	if is_restricted():
		frappe.throw(_("Cohort views require a reviewer role"), frappe.PermissionError)


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

	# Who is stuck: practising with repeated evidence and nothing demonstrated. The
	# threshold is deliberately low and deliberately visible, not hidden in a score.
	stuck = [s for s in states if s.state == PRACTISING and (s.evidence_count or 0) >= 3]

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
