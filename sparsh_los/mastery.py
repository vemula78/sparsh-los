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

# Ordered weakest to strongest. "Refresh Due" is deliberately absent: it is a valid
# stored state but no time-based transition is in scope for this engine.
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
			"recorded_at",
			"creation",
		],
		order_by="creation asc",
	)


def _sort_key(row):
	return (row.recorded_at or row.creation, row.creation)


def _independent_passes(rows):
	return [
		r
		for r in rows
		if r.outcome == "Pass" and not r.critical_error and (r.assistance_level or 0) == 0
	]


def has_blocking_critical_error(learner, competency) -> bool:
	"""True when a critical error stands unanswered by a later independent pass."""
	rows = _submitted_evidence(learner, competency)
	critical = [r for r in rows if r.critical_error]
	if not critical:
		return False

	passes = _independent_passes(rows)
	if not passes:
		return True

	return _sort_key(critical[-1]) > _sort_key(passes[-1])


def derive_state(learner, competency) -> str:
	"""Derive the mastery state for one (learner, competency) pair."""
	rows = _submitted_evidence(learner, competency)
	if not rows:
		return NOT_STARTED

	passes = _independent_passes(rows)
	distinct_activities = {r.activity for r in passes if r.activity}

	if len(distinct_activities) >= 2 or (not distinct_activities and len(passes) >= 2):
		state = MASTERED
	elif passes:
		state = DEMONSTRATED
	elif any(r.outcome in ("Pass", "Partial") for r in rows):
		state = PRACTISING
	else:
		state = EXPLORING

	if has_blocking_critical_error(learner, competency):
		# A standing critical error caps progression: competence cannot be claimed
		# while the most recent evidence of unsafe practice is unanswered.
		state = min(state, PRACTISING, key=STATE_ORDER.index)

	return state


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
		frappe.flags.in_mastery_recompute = False

	return doc.name
