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

from sparsh_los.mastery import DEMONSTRATED, MASTERED, PRACTISING, REJECTED
from sparsh_los.permissions import is_restricted, require_enrolment, require_reviewer

CERTIFIABLE = (DEMONSTRATED, MASTERED)

# A learner with no Evidence at all has no Mastery State row, so the evidence-side
# threshold above (three pieces of evidence) can never catch them. Three failing
# attempts is the same intent applied to the only record they have produced.
FAILING_ATTEMPTS_BEFORE_STUCK = 3


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

		# Rejected evidence is excluded from the state in `mastery.derive_state`, so
		# counting it here made the supervisor's explanation contradict the very state it
		# was explaining -- three rejected assisted passes read as "Passing only with
		# help" when nothing supported competence at all.
		# Filtered in Python, not in the query: `db.count` does not ifnull-wrap `!=`, so
		# `human_review_status != "Rejected"` drops every row whose status is NULL --
		# which is every piece of evidence nobody has reviewed, i.e. nearly all of it.
		evidence = frappe.get_all(
			"Sparsh Evidence",
			filters={"learner": row.learner, "competency": row.competency, "docstatus": 1},
			fields=["outcome", "assistance_level", "human_review_status"],
		)
		judged = [e for e in evidence if e.human_review_status != REJECTED]
		assisted = sum(1 for e in judged if e.outcome == "Pass" and (e.assistance_level or 0) > 0)
		failures = sum(1 for e in judged if e.outcome == "Fail")
		partials = sum(1 for e in judged if e.outcome == "Partial")

		# "Mixed results" was the catch-all, and it caught histories that are not mixed
		# at all: three Partials produced assisted=0, failures=0 and a supervisor told
		# there were mixed results when there was one consistent pattern. A reason a
		# supervisor cannot act on is worse than no reason.
		if assisted and not failures and not partials:
			reason = "Passing only with help"
		elif failures and not assisted and not partials:
			reason = "Repeated failures"
		elif partials and not assisted and not failures:
			reason = "Partial completions only"
		elif not (assisted or failures or partials):
			reason = "No unaided pass yet on a second activity"
		else:
			reason = "Mixed results without an unaided pass"

		stuck.append(
			dict(row, reason=reason, assisted_passes=assisted, failures=failures,
				 partial_results=partials)
		)

	# A learner who has never passed is the one a supervisor most needs to see, and
	# until now they were the one learner who could not appear here at all. The runner
	# writes Evidence on a pass or a critical error and on nothing else, so somebody who
	# answers wrongly six times produces six Attempts, no Evidence, and no Mastery State
	# row -- so they were absent from `states`, and this loop never considered them.
	# "Repeated failures" above was unreachable from the runner path for the same
	# reason: a Fail only becomes Evidence when a reviewer records one.
	seen = {(row["learner"], row["competency"]) for row in stuck}
	seen.update((row.learner, row.competency) for row in states if row.state in CERTIFIABLE)

	attempt_filters = {"outcome": ("not in", ("Pass", "Not Evaluated"))}
	failing = frappe.get_all(
		"Sparsh Attempt",
		filters=attempt_filters,
		fields=["learner", "activity", "attempted_at"],
		order_by="creation asc",
	)
	# The attempt carries the activity, not the competency, so the mapping is read once
	# rather than per row.
	activity_competency = {
		row.name: row.competency
		for row in frappe.get_all("Sparsh Activity", fields=["name", "competency"])
	}

	failing_by_pair = {}
	for row in failing:
		pair_competency = activity_competency.get(row.activity)
		if not pair_competency:
			continue
		if competency and pair_competency != competency:
			continue
		pair = (row.learner, pair_competency)
		entry = failing_by_pair.setdefault(pair, {"count": 0, "last": None})
		entry["count"] += 1
		entry["last"] = row.attempted_at or entry["last"]

	for (learner, pair_competency), entry in sorted(failing_by_pair.items()):
		if (learner, pair_competency) in seen:
			# Already reported above, with a reason drawn from their evidence.
			continue
		if entry["count"] < FAILING_ATTEMPTS_BEFORE_STUCK:
			continue

		stuck.append(
			{
				"learner": learner,
				"competency": pair_competency,
				"state": None,
				"last_demonstrated": None,
				"evidence_count": 0,
				"reason": "Repeated failures",
				"assisted_passes": 0,
				"failures": entry["count"],
				"partial_results": 0,
				"last_attempt": entry["last"],
			}
		)

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

	# A reporting window, not free input: a negative value silently produced a cutoff
	# in the future (so every count read zero), and an enormous one an unbounded scan.
	try:
		days = int(days)
	except (TypeError, ValueError):
		frappe.throw(_("Reporting period must be a whole number of days"))
	if days < 1 or days > 3650:
		frappe.throw(_("Reporting period must be between 1 and 3650 days"))

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

	# The same predicate the reviewer's queue uses. Counting every `Not Evaluated`
	# attempt instead counted the reviewed ones for ever: an attempt is immutable, so a
	# reviewed one keeps that outcome and the figure only ever grew, never agreeing with
	# the queue it claimed to describe.
	awaiting_person = frappe.db.sql(
		"""
		select count(*) from `tabSparsh Attempt` a
		where a.outcome = 'Not Evaluated'
		  and not exists (select 1 from `tabSparsh Evidence` e where e.attempt = a.name)
		"""
	)[0][0]
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
