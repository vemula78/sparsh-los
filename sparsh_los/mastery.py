# Copyright (c) 2026, SSSIHMS and contributors
# For license information, please see license.txt

"""Deterministic mastery derivation.

Mastery is never asserted by a user or by a model: it is recomputed from submitted
Evidence every time Evidence is submitted or cancelled. Nothing here performs any
network call, and nothing here consults anything other than the Evidence table.
"""

import frappe

NOT_STARTED = "Not Started"
EXPLORING = "Exploring"
PRACTISING = "Practising"
DEMONSTRATED = "Demonstrated"
MASTERED = "Mastered"

# Ordered weakest to strongest. "Refresh Due" is deliberately absent from the order: it
# is not a rung on the ladder but a flag that a strong state has gone stale, so it has
# no meaningful position relative to Practising.
REFRESH_DUE = "Refresh Due"

STATE_ORDER = (NOT_STARTED, EXPLORING, PRACTISING, DEMONSTRATED, MASTERED)


def _submitted_evidence(learner, competency):
	"""All submitted Evidence for the pair, oldest first."""
	return frappe.get_all(
		"Sparsh Evidence",
		filters={"learner": learner, "competency": competency, "docstatus": 1},
		fields=[
			"name",
			"activity",
			"outcome",
			"assistance_level",
			"critical_error",
			"critical_error_cleared",
			"human_review_status",
			"recorded_at",
			"creation",
		],
		order_by="creation asc",
	)


def _sort_key(row):
	# Order by `creation` alone. It is assigned by the database and cannot be supplied
	# by a caller, so ordering cannot be manipulated by backdating `recorded_at`.
	# `name` breaks ties: identical microsecond timestamps are possible under
	# concurrency, and a tie must not silently read as "the critical error was answered".
	return (row.creation, row.name)


# Evidence a reviewer has explicitly rejected is not evidence of competence.
REJECTED = "Rejected"


def _independent_passes(rows):
	"""Unaided passes that cite the activity they were earned on.

	Evidence with no activity carries no provenance, so it cannot establish that a
	learner demonstrated anything in particular. Evidence a reviewer rejected does
	not count either: the field existed and nothing read it, so a rejected pass
	counted exactly like an approved one.
	"""
	return [
		r
		for r in rows
		if r.outcome == "Pass"
		and not r.critical_error
		and (r.assistance_level or 0) == 0
		and r.activity
		and r.human_review_status != REJECTED
	]


def has_blocking_critical_error(learner, competency) -> bool:
	"""True when a critical error stands unanswered by a later independent pass."""
	rows = _submitted_evidence(learner, competency)

	# A safety error is cleared by a reviewer deciding it is cleared, never by the
	# learner performing well afterwards. Progression on aggregate performance is
	# exactly what the gate exists to prevent.
	return any(r.critical_error and not r.critical_error_cleared for r in rows)


def derive_state(learner, competency) -> str:
	"""Derive the mastery state for one (learner, competency) pair."""
	rows = _submitted_evidence(learner, competency)
	if not rows:
		return NOT_STARTED

	passes = _independent_passes(rows)
	distinct_activities = {r.activity for r in passes if r.activity}

	# Mastery requires independent passes across two distinct activities. Passes with no
	# activity recorded cannot establish it: provenance matters most exactly here.
	if len(distinct_activities) >= 2:
		state = MASTERED
	elif passes:
		state = DEMONSTRATED
	elif any(
		r.outcome in ("Pass", "Partial") and r.human_review_status != REJECTED for r in rows
	):
		state = PRACTISING
	else:
		state = EXPLORING

	if has_blocking_critical_error(learner, competency):
		# A standing critical error caps progression: competence cannot be claimed
		# while the most recent evidence of unsafe practice is unanswered.
		if STATE_ORDER.index(state) > STATE_ORDER.index(PRACTISING):
			state = PRACTISING

	# Applied last, and never before the cap: Refresh Due is outside STATE_ORDER, so
	# any ordering comparison must already be done by the time it is set.
	if state in (DEMONSTRATED, MASTERED) and (
		_refresh_overdue(learner, competency) or _has_open_refresher(learner, competency)
	):
		state = REFRESH_DUE

	return state


def _has_open_refresher(learner, competency):
	"""True while a refresher stands unfinished.

	Time is not the only thing that makes a demonstration stale. A rule superseded
	underneath the learner assigned a refresher but left the state at Demonstrated and
	the certificate Active, so the person went on being certified against a rule the
	programme had already replaced. The time-based path persisted that regression from
	the start; this is the same rule applied to the other two triggers.

	Refresher Assignment is itself derived — the engine writes it, no role creates one
	— so reading it here does not make mastery user-writable.
	"""
	return bool(
		frappe.db.exists(
			"Sparsh Refresher Assignment",
			{"learner": learner, "competency": competency, "status": "Assigned"},
		)
	)


def _refresh_overdue(learner, competency):
	"""True when the last demonstration is older than the competency's interval.

	Competence is a claim about now, not a claim about the past. A competency with
	no interval set never expires on time alone.
	"""
	interval = frappe.db.get_value("Sparsh Competency", competency, "refresh_interval_days")
	if not interval:
		return False

	last = _last_demonstrated(learner, competency)
	if not last:
		return False

	age = frappe.utils.date_diff(frappe.utils.now_datetime(), last)
	return age is not None and age > int(interval)


def _last_demonstrated(learner, competency):
	passes = _independent_passes(_submitted_evidence(learner, competency))
	if not passes:
		return None
	latest = max(passes, key=_sort_key)
	return latest.recorded_at or latest.creation


def recompute_mastery(learner, competency):
	"""Create or rewrite the Mastery State document for one pair. Idempotent."""
	if not (learner and competency):
		return

	rows = _submitted_evidence(learner, competency)
	name = frappe.db.get_value(
		"Sparsh Mastery State", {"learner": learner, "competency": competency}, "name"
	)

	previous_flag = frappe.flags.in_mastery_recompute
	frappe.flags.in_mastery_recompute = True
	try:
		if name:
			doc = frappe.get_doc("Sparsh Mastery State", name)
		else:
			doc = frappe.new_doc("Sparsh Mastery State")
			doc.learner = learner
			doc.competency = competency

		# state is recomputed inside the controller's validate()
		doc.last_demonstrated = _last_demonstrated(learner, competency)
		doc.evidence_count = len(rows)
		doc.set("supporting_evidence", [])
		for row in rows:
			doc.append(
				"supporting_evidence",
				{
					"evidence": row.name,
					"outcome": row.outcome,
					"recorded_at": row.recorded_at or row.creation,
				},
			)
		doc.save(ignore_permissions=True)
	finally:
		frappe.flags.in_mastery_recompute = previous_flag

	_reconcile_certifications(learner, competency, doc.state)

	return doc.name


def _reconcile_certifications(learner, competency, state):
	"""A certificate must not outlive the evidence that justified it.

	Certification records are submitted and therefore immutable, so the current
	standing is carried on an allow-on-submit field rather than by editing history.
	"""
	supported = state in (DEMONSTRATED, MASTERED) and not has_blocking_critical_error(
		learner, competency
	)
	target = "Active" if supported else "Suspended"

	for row in frappe.get_all(
		"Sparsh Certification Record",
		filters={"learner": learner, "competency": competency, "docstatus": 1},
		fields=["name", "certification_state", "certification_status"],
	):
		# A revoked certification stays revoked: evidence cannot un-revoke a governance
		# decision.
		if row.certification_status == "Revoked" or row.certification_state == "Revoked":
			continue

		if row.certification_state == target:
			continue

		frappe.db.set_value("Sparsh Certification Record", row.name, "certification_state", target)
