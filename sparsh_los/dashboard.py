# Copyright (c) 2026, SSSIHMS and contributors
# For license information, please see license.txt

"""Read models for the learner and supervisor views.

The supervisor view answers four questions and no more: who is ready to progress,
who is stuck and in which competency, where critical safety errors are occurring,
and what recurring questions should trigger a content or programme change.

Everything here is derived at read time from Evidence and Mastery State. Nothing is
stored, so nothing can drift from the evidence it claims to summarise.
"""

import statistics

import frappe
from frappe import _

from sparsh_los.mastery import (
	DEMONSTRATED,
	EXPLORING,
	MASTERED,
	NOT_STARTED,
	PRACTISING,
	REFRESH_DUE,
	REJECTED,
)
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

	# Fetched once and reused by the per-competency breakdown and the no-model-call
	# share below, so those figures describe exactly the attempts that `active_in_period`
	# was counted from. Two queries with two predicates is how figures drift apart.
	period_attempts = frappe.get_all(
		"Sparsh Attempt",
		filters={"creation": (">", since)},
		fields=["learner", "activity", "outcome", "hint_level_used", "retry_index", "critical_error"],
	)
	active = {row.learner for row in period_attempts}

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
	# The same predicate the reviewer's queue uses, including the Reflection exclusion:
	# a reflection is stored, not queued, so counting one here would report work waiting
	# on a person that no person will ever be shown. Counting every `Not Evaluated`
	# attempt -- the version before this -- also counted the reviewed ones for ever,
	# because an attempt is immutable and keeps that outcome.
	from sparsh_los.runner import NOT_SCORED_MODES

	awaiting_person = frappe.db.sql(
		"""
		select count(*)
		from `tabSparsh Attempt` a
		inner join `tabSparsh Activity` act on act.name = a.activity
		where a.outcome = 'Not Evaluated'
		  and ifnull(act.evaluation_mode, '') not in %(not_scored)s
		  and not exists (select 1 from `tabSparsh Evidence` e where e.attempt = a.name)
		""",
		{"not_scored": NOT_SCORED_MODES},
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

	activities = {
		row.name: row
		for row in frappe.get_all("Sparsh Activity", fields=["name", "competency", "evaluation_mode"])
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
		# Section 18's remaining core metrics. Each block carries its own numerator and
		# denominator, and where a figure cannot be computed it says so rather than
		# reading zero.
		"inactivity": _inactivity(learners, active, since),
		"refreshers": _refresher_participation(since),
		"escalation_turnaround": _escalation_turnaround(since),
		"competency_state_changes": _state_changes(since),
		"attempts_by_competency": _attempts_by_competency(period_attempts, activities),
		"pathway_completion": _pathway_completion(),
		"no_model_call_share": _no_model_call_share(period_attempts, activities, since),
	}


def _inactivity(learners, active, since):
	"""Enrolled learners with no attempt in the period, and when they were last seen.

	Inactive is defined as the complement of `active_in_period` over the enrolled set,
	so `inactive + active_in_period == enrolled` holds exactly. Activity for the *date*
	is wider than activity for the *count*: the last-seen date takes the later of the
	learner's last Attempt and last Event, because a learner who opened a session and
	answered nothing has still been seen. A learner who is inactive by the count but has
	an Event inside the period is therefore reported separately rather than folded into
	either figure.
	"""
	if not learners:
		return {
			"inactive": None,
			"enrolled": 0,
			"proportion": None,
			"reason": "No account holds the learner role, so there is no enrolled set to be inactive from.",
			"learners": [],
		}

	inactive = sorted(learners - active)
	if not inactive:
		return {"inactive": 0, "enrolled": len(learners), "proportion": 0.0, "learners": [],
				"inactive_with_event_in_period": 0}

	last_attempt = {
		row.learner: row.last
		for row in frappe.db.sql(
			"""select learner, max(creation) as last from `tabSparsh Attempt`
			   where learner in %(learners)s group by learner""",
			{"learners": inactive},
			as_dict=True,
		)
	}
	# Only what the learner did. Every event type carries `learner`, including the ones a
	# reviewer or the engine causes -- human_review_completed, mastery_state_changed,
	# refresher_assigned, certification_state_changed -- so an unfiltered max() reported
	# a learner as last seen on the day somebody else acted about them.
	from sparsh_los import events

	last_event = {
		row.learner: row.last
		for row in frappe.db.sql(
			"""select learner, max(occurred_at) as last from `tabSparsh Event`
			   where learner in %(learners)s
			     and event_type in %(learner_initiated)s
			   group by learner""",
			{"learners": inactive, "learner_initiated": events.LEARNER_INITIATED},
			as_dict=True,
		)
	}

	rows = []
	seen_in_period = 0
	for learner in inactive:
		candidates = [
			(stamp, source)
			for stamp, source in ((last_attempt.get(learner), "attempt"), (last_event.get(learner), "event"))
			if stamp
		]
		if candidates:
			last, source = max(candidates)
			if last > since:
				seen_in_period += 1
		else:
			last, source = None, None
		rows.append({"learner": learner, "last_activity": last, "source": source})

	return {
		"inactive": len(inactive),
		"enrolled": len(learners),
		"proportion": round(len(inactive) / len(learners), 4),
		# Non-zero here means an Event but no Attempt in the period: seen, but did no work.
		"inactive_with_event_in_period": seen_in_period,
		"learners": rows,
	}


def _refresher_participation(since):
	"""Refreshers issued, completed and repeated in the period. Overdue is undefined.

	`Sparsh Refresher Assignment` carries no due date and the programme has set no
	policy for one, so "overdue" cannot be counted from the record. It is reported as
	None with the reason, not as zero: zero would read as "nothing is overdue".

	Issued and completed are counted on their own dates, so a refresher issued before
	the period and completed inside it is in `completed` and not in `issued`; the two
	are not a numerator and denominator of each other. `open_now` is the standing
	backlog regardless of date.
	"""
	rows = frappe.get_all(
		"Sparsh Refresher Assignment",
		fields=["name", "learner", "competency", "status", "assigned_on", "completed_on", "creation"],
		order_by="creation asc",
	)

	issued = [r for r in rows if (r.assigned_on or r.creation) > since]
	completed = [r for r in rows if r.status == "Completed" and r.completed_on and r.completed_on > since]
	# Completed without a completion stamp cannot be placed in any period. Reported, not dropped.
	completed_undated = sum(1 for r in rows if r.status == "Completed" and not r.completed_on)

	# Repeated: an assignment issued in the period for a learner and competency that
	# already had an earlier assignment, whatever its status or trigger. A second
	# refresher on the same competency is the signal that one did not take.
	issued_names = {r.name for r in issued}
	earlier_pairs = set()
	repeated = 0
	for r in rows:
		pair = (r.learner, r.competency)
		if r.name in issued_names and pair in earlier_pairs:
			repeated += 1
		earlier_pairs.add(pair)

	return {
		"issued": len(issued),
		"completed": len(completed),
		"completed_without_completion_date": completed_undated,
		"repeated": repeated,
		"open_now": sum(1 for r in rows if r.status == "Assigned"),
		"cancelled_in_period": sum(1 for r in rows if r.status == "Cancelled" and (r.assigned_on or r.creation) > since),
		"overdue": None,
		"overdue_undefined_because": (
			"Sparsh Refresher Assignment has no due date and the programme has not set a "
			"refresher deadline policy; overdue cannot be counted until it does."
		),
	}


def _escalation_turnaround(since):
	"""Time from a question being raised to being answered, for answers in the period."""
	rows = frappe.get_all(
		"Sparsh Escalation Question",
		fields=["name", "status", "raised_at", "answered_at"],
	)

	answered = [r for r in rows if r.answered_at and r.answered_at > since]
	timed = [r for r in answered if r.raised_at]
	hours = [
		frappe.utils.time_diff_in_seconds(r.answered_at, r.raised_at) / 3600 for r in timed
	]
	negative = sum(1 for h in hours if h < 0)

	return {
		"answered_in_period": len(answered),
		"answered_without_raised_at": len(answered) - len(timed),
		"median_hours": round(statistics.median(hours), 2) if hours else None,
		"median_undefined_because": None if hours else "No question with both timestamps was answered in the period.",
		# A negative interval means the clocks disagree, not that an answer preceded its question.
		"answered_before_raised": negative,
		"still_open_raised_in_period": sum(
			1
			for r in rows
			if r.status in ("Open", "Routed to Human") and r.raised_at and r.raised_at > since
		),
		# `open_questions` at the top level is the all-time backlog; this is the slice of it
		# raised inside the window, so the two differ by the questions older than the period.
	}


_STATE_RANK = {
	"none": 0,
	NOT_STARTED: 0,
	EXPLORING: 1,
	PRACTISING: 2,
	# A regression from Demonstrated or Mastered, ranked with Practising: the learner
	# still holds the evidence, so Refresh Due -> Demonstrated is an improvement and
	# Practising -> Refresh Due, were it ever to occur, is lateral rather than a fall.
	REFRESH_DUE: 2,
	DEMONSTRATED: 3,
	MASTERED: 4,
}


def _state_changes(since):
	"""Mastery-state transitions in the period, per competency, from the event log.

	`mastery.recompute_mastery` emits one `mastery_state_changed` event per transition
	with `detail` of the form "previous -> new" (previous is "none" for a first state).
	The log is the only record of transitions -- Mastery State stores the current state
	and nothing else -- so this is counted from events and a detail that does not parse
	is reported, not skipped.
	"""
	from sparsh_los import events

	rows = frappe.get_all(
		"Sparsh Event",
		filters={"event_type": events.MASTERY_STATE_CHANGED, "occurred_at": (">", since)},
		fields=["competency", "detail"],
	)

	per = {}
	unparsed = 0
	for row in rows:
		parts = [p.strip() for p in (row.detail or "").split("->")]
		if len(parts) != 2 or parts[0] not in _STATE_RANK or parts[1] not in _STATE_RANK:
			unparsed += 1
			continue
		previous, new = parts
		bucket = per.setdefault(
			row.competency, {"changes": 0, "improved": 0, "regressed": 0, "lateral": 0, "transitions": {}}
		)
		bucket["changes"] += 1
		delta = _STATE_RANK[new] - _STATE_RANK[previous]
		bucket["improved" if delta > 0 else "regressed" if delta < 0 else "lateral"] += 1
		key = f"{previous} -> {new}"
		bucket["transitions"][key] = bucket["transitions"].get(key, 0) + 1

	return {"events": len(rows), "unparsed": unparsed, "by_competency": per}


def _attempts_by_competency(period_attempts, activities):
	"""Attempts, retries and hint levels per competency, from the period's attempts.

	The attempt carries the activity and the activity carries the competency. An
	attempt on an activity that no longer exists, or one without a competency, is
	counted under `unattributed` so the per-competency figures still sum to the total.
	"""
	per = {}
	unattributed = 0
	for row in period_attempts:
		activity = activities.get(row.activity)
		competency = activity.competency if activity else None
		if not competency:
			unattributed += 1
			continue
		bucket = per.setdefault(
			competency,
			{"attempts": 0, "retries": 0, "assisted": 0, "critical_errors": 0, "hint_levels": {}, "outcomes": {}},
		)
		bucket["attempts"] += 1
		if (row.retry_index or 0) > 0:
			bucket["retries"] += 1
		level = row.hint_level_used or 0
		if level > 0:
			bucket["assisted"] += 1
		bucket["hint_levels"][level] = bucket["hint_levels"].get(level, 0) + 1
		outcome = row.outcome or "None"
		bucket["outcomes"][outcome] = bucket["outcomes"].get(outcome, 0) + 1
		if row.critical_error:
			bucket["critical_errors"] += 1

	return {"attempts": len(period_attempts), "unattributed": unattributed, "by_competency": per}


def _pathway_completion():
	"""Of the pathway steps assigned through Active cohorts, how many are completed.

	Assigned means: the learner is a member of an Active cohort whose pathway is Active
	-- the same condition under which `orchestrator.next_in_pathway` hands out work. A
	cohort whose pathway is Draft or Retired assigns nothing, and is counted so the
	omission is visible.

	Completed uses the orchestrator's own rule for a mandatory step -- a submitted,
	unassisted Pass on the step's activity with no critical error and no rejection -- so
	this figure cannot say a pathway is complete while `next_in_pathway` still owes a
	step. A step naming only a competency is complete when that competency is
	Demonstrated or Mastered. An assisted pass is counted separately: it is what
	`_passed_activities` credits, but it does not complete the step.

	Not windowed: completion is a position, not an event.
	"""
	cohorts = frappe.get_all(
		"Sparsh Cohort", filters={"status": "Active"}, fields=["name", "pathway"]
	)
	pathway_status = {
		row.name: row.status
		for row in frappe.get_all("Sparsh Pathway", fields=["name", "status"])
	}
	usable = [c for c in cohorts if c.pathway and pathway_status.get(c.pathway) == "Active"]

	result = {
		"active_cohorts": len(cohorts),
		"active_cohorts_without_active_pathway": len(cohorts) - len(usable),
		"learners_assigned": 0,
		"learners_in_more_than_one_active_cohort": 0,
		"steps_assigned": 0,
		"steps_completed": 0,
		"steps_passed_assisted_only": 0,
		"learners_completed_all_steps": 0,
		"proportion": None,
		"by_pathway": {},
	}
	if not usable:
		result["reason"] = "No Active cohort follows an Active pathway, so no steps are assigned."
		return result

	membership = frappe.get_all(
		"Sparsh Cohort Member",
		filters={"parent": ("in", [c.name for c in usable]), "parenttype": "Sparsh Cohort"},
		fields=["parent", "learner"],
	)
	learners = {m.learner for m in membership}
	counts = {}
	for m in membership:
		counts[m.learner] = counts.get(m.learner, 0) + 1
	result["learners_in_more_than_one_active_cohort"] = sum(1 for n in counts.values() if n > 1)
	result["learners_assigned"] = len(learners)
	if not learners:
		result["reason"] = "The Active cohorts have no members."
		return result

	steps_by_pathway = {}
	for row in frappe.get_all(
		"Sparsh Pathway Step",
		filters={"parent": ("in", sorted({c.pathway for c in usable})), "parenttype": "Sparsh Pathway"},
		fields=["parent", "activity", "competency"],
	):
		steps_by_pathway.setdefault(row.parent, []).append(row)
	activity_competency = {
		row.name: row.competency for row in frappe.get_all("Sparsh Activity", fields=["name", "competency"])
	}

	# Rejection filtered in Python: `!=` on a NULL review status behaves differently
	# between `get_all` and `db.count`, and nearly every row here is NULL.
	evidence = frappe.get_all(
		"Sparsh Evidence",
		filters={"learner": ("in", sorted(learners)), "outcome": "Pass", "critical_error": 0, "docstatus": 1},
		fields=["learner", "activity", "assistance_level", "human_review_status"],
	)
	unassisted, any_pass = set(), set()
	for e in evidence:
		if e.human_review_status == REJECTED or not e.activity:
			continue
		any_pass.add((e.learner, e.activity))
		if (e.assistance_level or 0) == 0:
			unassisted.add((e.learner, e.activity))
	certifiable = {
		(s.learner, s.competency)
		for s in frappe.get_all(
			"Sparsh Mastery State",
			filters={"learner": ("in", sorted(learners)), "state": ("in", CERTIFIABLE)},
			fields=["learner", "competency"],
		)
	}

	cohort_pathway = {c.name: c.pathway for c in usable}
	for m in membership:
		pathway = cohort_pathway[m.parent]
		steps = steps_by_pathway.get(pathway, [])
		bucket = result["by_pathway"].setdefault(
			pathway, {"learners": 0, "steps_assigned": 0, "steps_completed": 0}
		)
		bucket["learners"] += 1
		done = 0
		assisted_only = 0
		for step in steps:
			if step.activity:
				if (m.learner, step.activity) in unassisted:
					done += 1
				elif (m.learner, step.activity) in any_pass:
					assisted_only += 1
			else:
				competency = step.competency
				if competency and (m.learner, competency) in certifiable:
					done += 1
		bucket["steps_assigned"] += len(steps)
		bucket["steps_completed"] += done
		result["steps_assigned"] += len(steps)
		result["steps_completed"] += done
		result["steps_passed_assisted_only"] += assisted_only
		if steps and done == len(steps):
			result["learners_completed_all_steps"] += 1

	if result["steps_assigned"]:
		result["proportion"] = round(result["steps_completed"] / result["steps_assigned"], 4)
	else:
		result["reason"] = "The assigned pathways have no steps."
	return result


# The one evaluation mode whose evaluator, once built, would call a model. Every other
# mode is deterministic, a person, or the learner's own reflection.
MODEL_MODES = ("AI-assisted",)


def _no_model_call_share(period_attempts, activities, since):
	"""Section 20: the share of learner interactions that needed no model call.

	Two independent readings, both reported. By *mode*: attempts on activities whose
	evaluation mode is not AI-assisted, over all attempts in the period. By *ledger*:
	attempts minus the `Sparsh Model Interaction` rows recorded in the same window,
	because every model call is required to go through `gateway.record`. While no
	evaluator calls a model the ledger is empty and the two readings differ by exactly
	the number of AI-assisted attempts; when they disagree the disagreement is stated,
	not resolved by picking one.
	"""
	total = len(period_attempts)
	on_model_modes = 0
	unattributed = 0
	for row in period_attempts:
		activity = activities.get(row.activity)
		if activity is None:
			unattributed += 1
			continue
		if (activity.evaluation_mode or "") in MODEL_MODES:
			on_model_modes += 1

	model_interactions = frappe.db.count("Sparsh Model Interaction", {"occurred_at": (">", since)})

	def share(numerator):
		return round(numerator / total, 4) if total else None

	by_mode = total - on_model_modes
	by_ledger = total - model_interactions
	return {
		"target": "0.80-0.90 (design target, section 20)",
		"attempts": total,
		"attempts_on_unknown_activity": unattributed,
		"by_mode": {"numerator": by_mode, "denominator": total, "share": share(by_mode)},
		"by_ledger": {
			"numerator": by_ledger,
			"denominator": total,
			"share": share(by_ledger) if by_ledger >= 0 else None,
			"model_interactions_in_period": model_interactions,
		},
		"readings_agree": on_model_modes == model_interactions,
		"disagreement": (
			None
			if on_model_modes == model_interactions
			else f"{on_model_modes} attempt(s) on AI-assisted activities against {model_interactions} "
			"recorded model interaction(s); the unbuilt AI-assisted evaluator makes no call, so "
			"until it exists the ledger reading is the one that describes what ran."
		),
		"share_undefined_because": None if total else "No attempts in the period.",
	}
