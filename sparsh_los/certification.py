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
	REFRESH_DUE,
	_last_demonstrated,
	derive_state,
	has_blocking_critical_error,
)
from sparsh_los.permissions import is_restricted, require_enrolment, require_reviewer

READY = "Ready for sign-off"
BLOCKED = "Blocked by a safety error"
INSUFFICIENT = "Insufficient evidence"

CERTIFICATE_FIELDS = [
	"name",
	"certification_status",
	"certification_state",
	"certified_on",
	"certified_by",
	"certificate_version",
	"renewal_due",
]


def _version_or_none(value):
	"""0 is the column default, not a version: it means the ordinal is not known."""
	return int(value) if value else None


def _certificates(learner, competency, docstatus=(1, 2)):
	"""Certification records for the pair, oldest first, versions normalised."""
	rows = frappe.get_all(
		"Sparsh Certification Record",
		filters={
			"learner": learner,
			"competency": competency,
			"docstatus": ("in", docstatus) if isinstance(docstatus, tuple) else docstatus,
		},
		fields=CERTIFICATE_FIELDS + ["docstatus", "revokes"],
		order_by="creation asc",
	)
	for row in rows:
		# A revocation record withdraws a certificate; it is not one, so it has no
		# version of its own.
		row["certificate_version"] = (
			None if row.certification_status == "Revoked" else _version_or_none(row.certificate_version)
		)
	return rows


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
			"human_review_status",
			"recorded_at",
		],
		order_by="creation asc",
	)

	# The same filter as mastery._independent_passes except for its `and r.activity`
	# term, which is enforced upstream: Sparsh Evidence refuses to store an unaided
	# pass with no activity. The counts therefore agree, but the guarantee lives in the
	# Evidence controller, not here.
	independent = [
		r
		for r in rows
		if r.outcome == "Pass"
		and not r.critical_error
		and not r.assistance_level
		and r.human_review_status != "Rejected"
	]
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
	require_enrolment()
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
	elif state == REFRESH_DUE:
		# A learner held by a refresher may already have every unaided pass the rule
		# asks for. Telling them to earn another sends them at the wrong task and reads
		# as though their prior work stopped counting, which is exactly what the
		# refresher design promises does not happen.
		verdict = INSUFFICIENT
		uncleared = []
		reason = _(
			"A refresher is outstanding. Prior evidence stands; completing the assigned "
			"refresher restores the competency."
		)
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
		"certifications": _certificates(learner, competency, docstatus=1),
	}


@frappe.whitelist()
def cohort_readiness(competency, cohort=None):
	"""Who is ready, who is blocked, and who needs more evidence — with reasons.

	Without `cohort`, everyone holding a Mastery State for the competency. With it,
	that cohort's members -- all of them, including a member with no state yet, who
	reports as Insufficient; a cohort progress figure that omits the learners who
	have not started overstates the cohort.
	"""
	require_reviewer()

	if cohort:
		from sparsh_los.cohort import member_learners

		if not frappe.db.exists("Sparsh Cohort", cohort):
			frappe.throw(_("No such cohort"), frappe.DoesNotExistError)
		learners = set(member_learners(cohort))
	else:
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

	return {
		"competency": competency,
		"cohort": cohort,
		"counts": {k: len(v) for k, v in buckets.items()},
		"buckets": buckets,
	}


@frappe.whitelist()
def certificate_detail(name):
	"""Everything a certificate should be able to show, traced to its evidence.

	Section 16 requires certification to be traceable to specific competency evidence
	and human sign-off. A certificate that cannot show its working is a scorecard.
	"""
	require_enrolment()
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
		"certificate_version": _version_or_none(doc.certificate_version),
		"renewal_due": doc.renewal_due,
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
			# A row a reviewer explicitly rejected is not part of a certificate's
			# working, on the endpoint whose whole purpose is showing that working.
			if r.outcome == "Pass"
			and not r.critical_error
			and r.human_review_status != "Rejected"
		],
	}


# ------------------------------------------------------------- section 18, view 4
# The certification and compliance buckets. "Not yet ready" always carries the
# readiness reason, because the dashboard has to show why, not merely that.
CERTIFIED = "Certified"
RENEWAL_DUE = "Renewal due"
PENDING_SIGN_OFF = "Pending sign-off"
NOT_YET_READY = "Not yet ready"
COMPLIANCE_BUCKETS = (CERTIFIED, RENEWAL_DUE, PENDING_SIGN_OFF, NOT_YET_READY)

NO_RENEWAL_POLICY = "No renewal policy set"

# Every certification record is issued under one of these; a standing one is the
# certificate that answers "is this person certified right now?".
_STANDING = ("Active", "Suspended")


def _refresher_due(learner, competency, last_demonstrated):
	"""When time alone will make the last demonstration stale, or None.

	None when the competency has no interval or the learner has not demonstrated:
	a due date cannot be computed from nothing, and inventing one would be an
	operational value the programme never set.
	"""
	interval = frappe.db.get_value("Sparsh Competency", competency, "refresh_interval_days")
	if not interval or not last_demonstrated:
		return None
	return frappe.utils.add_days(frappe.utils.getdate(last_demonstrated), int(interval))


def _open_refresher(learner, competency):
	rows = frappe.get_all(
		"Sparsh Refresher Assignment",
		filters={"learner": learner, "competency": competency, "status": "Assigned"},
		fields=["name", "trigger_reason", "assigned_on"],
		order_by="creation asc",
		limit=1,
	)
	return rows[0] if rows else None


def _compliance_row(learner, competency):
	"""One learner on one competency, in the vocabulary of section 18 view 4.

	The "why" is `readiness()`'s own verdict and reason, taken verbatim. This view
	buckets that answer against the certificate that stands; it does not re-derive
	readiness, so the two endpoints cannot disagree about whether somebody is ready.
	"""
	ready = readiness(competency, learner)
	history = _certificates(learner, competency)
	standing = [
		c for c in history if c.docstatus == 1 and c.certification_state in _STANDING
	]
	# The unique index on standing_key holds this to at most one; the last is the
	# newest if a server-side write ever broke that.
	certificate = standing[-1] if standing else None

	today = frappe.utils.getdate()
	renewal_passed = bool(
		certificate and certificate.renewal_due and frappe.utils.getdate(certificate.renewal_due) <= today
	)

	if certificate:
		if ready["verdict"] == BLOCKED:
			# A safety error outranks everything else, a lapsed renewal date
			# included: "renewal due" would read as paperwork where the reason is
			# an unresolved unsafe act.
			bucket = NOT_YET_READY
		elif ready["state"] == REFRESH_DUE or renewal_passed:
			bucket = RENEWAL_DUE
		elif ready["verdict"] == READY:
			bucket = CERTIFIED
		else:
			# A standing certificate whose evidence no longer supports it: the
			# reconciler has suspended it, or will on the next recompute. The
			# learner is not certified in any sense the dashboard should claim.
			bucket = NOT_YET_READY
	else:
		bucket = PENDING_SIGN_OFF if ready["verdict"] == READY else NOT_YET_READY

	last = _last_demonstrated(learner, competency)

	return {
		"learner": learner,
		"competency": competency,
		"compliance_status": bucket,
		# Straight from readiness(): the verdict and the reason are one explanation.
		"state": ready["state"],
		"verdict": ready["verdict"],
		"reason": ready["reason"],
		"blocking_evidence": ready["blocking_evidence"],
		"evidence": {
			"evidence_count": ready["evidence_count"],
			"independent_passes": ready["independent_passes"],
			"distinct_activities": ready["distinct_activities"],
		},
		"sign_off": (
			{
				"certificate": certificate.name,
				"certified_by": certificate.certified_by,
				"certified_on": certificate.certified_on,
				"certification_status": certificate.certification_status,
				"certification_state": certificate.certification_state,
				"certificate_version": certificate.certificate_version,
				"version_known": certificate.certificate_version is not None,
			}
			if certificate
			else None
		),
		"last_demonstrated": last,
		"refresher_due": _refresher_due(learner, competency, last),
		"open_refresher": _open_refresher(learner, competency),
		"renewal_due": certificate.renewal_due if certificate else None,
		"renewal_policy": (
			str(certificate.renewal_due) if certificate and certificate.renewal_due else NO_RENEWAL_POLICY
		),
		"certificate_history": history,
	}


def _learner_cohort(learner):
	"""The learner's Active cohort for grouping, without refusing the whole report.

	`cohort.cohort_for` throws on two Active memberships, which is right for the
	engine choosing a pathway and wrong for a report: one unresolved learner would
	take the view down for everyone. Named as unresolved instead.
	"""
	from sparsh_los.cohort import _active_memberships

	rows = _active_memberships(learner)
	if not rows:
		return None
	if len(rows) > 1:
		return "unresolved: " + ", ".join(r.name for r in rows)
	return rows[0].name


def _scope(competency, cohort):
	"""The (learner, competency) pairs the view covers."""
	if cohort:
		from sparsh_los.cohort import member_learners

		if not frappe.db.exists("Sparsh Cohort", cohort):
			frappe.throw(_("No such cohort"), frappe.DoesNotExistError)
		learners = set(member_learners(cohort))
		learner_filter = {"learner": ("in", sorted(learners))} if learners else None
	else:
		learners = None
		learner_filter = {}

	pairs = set()
	if learner_filter is not None:
		filters = dict(learner_filter)
		if competency:
			filters["competency"] = competency
		# A learner with a certificate always has a state, but a cancelled or
		# revoked history may outlive the state row; take both sources.
		for doctype in ("Sparsh Mastery State", "Sparsh Certification Record"):
			for row in frappe.get_all(doctype, filters=filters, fields=["learner", "competency"]):
				pairs.add((row.learner, row.competency))

	if cohort and competency:
		# Every member reports on the named competency, including one who has not
		# started -- a rate that omits non-starters overstates the cohort.
		for learner in learners:
			pairs.add((learner, competency))

	return sorted(pairs)


@frappe.whitelist()
def compliance_view(competency=None, cohort=None):
	"""Section 18, view 4: certified / pending / not yet ready / renewal due, with why.

	Optionally narrowed to one competency, one cohort, or both. Nothing here
	certifies anybody: it reads the certificates people issued and the readiness the
	evidence supports, and reports where the two stand against each other.
	"""
	require_reviewer()
	if competency and not frappe.db.exists("Sparsh Competency", competency):
		frappe.throw(_("No such competency"), frappe.DoesNotExistError)

	rows = [_compliance_row(learner, comp) for learner, comp in _scope(competency, cohort)]

	counts = {bucket: 0 for bucket in COMPLIANCE_BUCKETS}
	for row in rows:
		counts[row["compliance_status"]] += 1

	# Aggregate rates by cohort. The denominator is every pair in scope for that
	# cohort, so a learner who has not started counts against the rate.
	by_cohort = {}
	for row in rows:
		key = cohort or _learner_cohort(row["learner"])
		entry = by_cohort.setdefault(
			key, {"pairs": 0, **{bucket: 0 for bucket in COMPLIANCE_BUCKETS}}
		)
		entry["pairs"] += 1
		entry[row["compliance_status"]] += 1
	for entry in by_cohort.values():
		entry["certified_rate"] = entry[CERTIFIED] / entry["pairs"] if entry["pairs"] else None

	# Aggregate by period: certificates issued and revoked per calendar month, from
	# the records in scope. Issue is dated by certified_on, which before_submit sets
	# at the moment of submission.
	by_period = {}
	for row in rows:
		for cert in row["certificate_history"]:
			if cert.docstatus != 1 or not cert.certified_on:
				continue
			period = frappe.utils.getdate(cert.certified_on).strftime("%Y-%m")
			entry = by_period.setdefault(period, {"issued": 0, "revoked": 0})
			entry["revoked" if cert.certification_status == "Revoked" else "issued"] += 1

	return {
		"competency": competency,
		"cohort": cohort,
		"counts": counts,
		"rows": rows,
		"by_cohort": dict(sorted(by_cohort.items(), key=lambda kv: str(kv[0]))),
		"by_period": dict(sorted(by_period.items())),
	}
