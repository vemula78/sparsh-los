# Copyright (c) 2026, SSSIHMS and contributors
# For license information, please see license.txt

"""Certification readiness.

The platform does not certify anybody. It compiles the evidence and says what that
evidence supports, and why a learner is not yet ready when they are not — which is
the question a programme manager actually has. The decision stays with a person.
"""

import frappe
from frappe import _

from sparsh_los.mastery import (
	DEMONSTRATED,
	MASTERED,
	derive_state,
	has_blocking_critical_error,
)
from sparsh_los.permissions import is_restricted, require_reviewer

READY = "Ready for sign-off"
BLOCKED = "Blocked by a safety error"
INSUFFICIENT = "Insufficient evidence"


def _evidence_summary(learner, competency):
	rows = frappe.get_all(
		"Sparsh Evidence",
		filters={"learner": learner, "competency": competency, "docstatus": 1},
		fields=[
			"name",
			"activity",
			"outcome",
			"assistance_level",
			"critical_error",
			"critical_error_cleared",
			"recorded_at",
		],
		order_by="creation asc",
	)

	independent = [r for r in rows if r.outcome == "Pass" and not r.critical_error and not r.assistance_level]
	return {
		"evidence_count": len(rows),
		"independent_passes": len(independent),
		"distinct_activities": sorted({r.activity for r in independent if r.activity}),
		"critical_errors": [r for r in rows if r.critical_error and not r.critical_error_cleared],
		"records": rows,
	}


@frappe.whitelist()
def readiness(competency, learner=None):
	"""Why this learner is or is not ready, with the evidence behind the answer."""
	learner = learner or frappe.session.user
	if learner != frappe.session.user and is_restricted():
		frappe.throw(_("You can only view your own readiness"), frappe.PermissionError)

	state = derive_state(learner, competency)
	summary = _evidence_summary(learner, competency)
	blocked = has_blocking_critical_error(learner, competency)

	if blocked:
		verdict = BLOCKED
		uncleared = [r.name for r in summary["critical_errors"]]
		reason = _("A critical error is unresolved and requires a reviewer to clear it.")
	elif state in (DEMONSTRATED, MASTERED):
		verdict = READY
		uncleared = []
		reason = _("The evidence supports sign-off. The decision remains with the programme.")
	else:
		verdict = INSUFFICIENT
		uncleared = []
		reason = _("Competency is at {0}; an unaided pass is needed to demonstrate it.").format(state)

	return {
		"learner": learner,
		"competency": competency,
		"state": state,
		"verdict": verdict,
		"reason": reason,
		"blocking_evidence": uncleared,
		"evidence_count": summary["evidence_count"],
		"independent_passes": summary["independent_passes"],
		"distinct_activities": summary["distinct_activities"],
		"certifications": frappe.get_all(
			"Sparsh Certification Record",
			filters={"learner": learner, "competency": competency, "docstatus": 1},
			fields=["name", "certification_status", "certification_state", "certified_on", "certified_by"],
		),
	}


@frappe.whitelist()
def cohort_readiness(competency):
	"""Who is ready, who is blocked, and who needs more evidence — with reasons."""
	require_reviewer()

	learners = {
		row.learner
		for row in frappe.get_all(
			"Sparsh Mastery State", filters={"competency": competency}, fields=["learner"]
		)
	}

	buckets = {READY: [], BLOCKED: [], INSUFFICIENT: []}
	for learner in sorted(learners):
		row = readiness(competency, learner)
		buckets[row["verdict"]].append(
			{"learner": learner, "state": row["state"], "reason": row["reason"]}
		)

	return {"competency": competency, "counts": {k: len(v) for k, v in buckets.items()}, "buckets": buckets}


@frappe.whitelist()
def certificate_detail(name):
	"""Everything a certificate should be able to show, traced to its evidence.

	Section 16 requires certification to be traceable to specific competency evidence
	and human sign-off. A certificate that cannot show its working is a scorecard.
	"""
	doc = frappe.get_doc("Sparsh Certification Record", name)
	if doc.learner != frappe.session.user and is_restricted():
		frappe.throw(_("You can only view your own certificate"), frappe.PermissionError)

	summary = _evidence_summary(doc.learner, doc.competency)

	return {
		"certificate": doc.name,
		"learner": doc.learner,
		"competency": doc.competency,
		"competency_name": frappe.db.get_value(
			"Sparsh Competency", doc.competency, "competency_name"
		),
		"status": doc.certification_status,
		"state": doc.certification_state,
		"certified_on": doc.certified_on,
		"certified_by": doc.certified_by,
		"derived_state_at_certification": doc.derived_state,
		"state_now": derive_state(doc.learner, doc.competency),
		"evidence_count": summary["evidence_count"],
		"independent_passes": summary["independent_passes"],
		"distinct_activities": summary["distinct_activities"],
		"supporting_evidence": [
			{
				"evidence": r.name,
				"activity": r.activity,
				"outcome": r.outcome,
				"assistance_level": r.assistance_level,
				"recorded_at": r.recorded_at,
			}
			for r in summary["records"]
			if r.outcome == "Pass" and not r.critical_error
		],
	}
