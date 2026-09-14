# Copyright (c) 2026, SSSIHMS and contributors
# For license information, please see license.txt

"""Can the pilot start, and what is outstanding?

`seed.programme_readiness` answers a narrower question: is anything configured such
that the engine would score against a rule nobody validated. That is about safety.
This is about *readiness* -- whether the people, the cohort and the content exist yet
-- and it is deliberately a separate report, because a programme manager asking "can we
begin on Monday" is not asking the same thing as a reviewer asking "is anything unsafe".

Everything here is read at call time from what the engine actually holds. Nothing is
stored, so the answer cannot drift from the thing it describes. Where a figure depends
on a decision nobody has taken, it says so rather than guessing: a pilot reported ready
when it is not is worse than no report.
"""

import frappe
from frappe import _

from sparsh_los.permissions import require_reviewer

# His words, 13-Sep-2026: "For the first pilot, I suggest 8-10 currently certified
# SAI SPARSH volunteers." A suggestion, so it is reported against rather than enforced.
SUGGESTED_COHORT_MIN = 8
SUGGESTED_COHORT_MAX = 10


def _pilot_pathway():
	from sparsh_los.seed import PILOT_PATHWAY

	return frappe.db.get_value(
		"Sparsh Pathway", PILOT_PATHWAY, ["name", "status"], as_dict=True
	)


def _reviewers():
	"""Accounts that can actually judge an attempt, not names on a document."""
	rows = frappe.get_all(
		"Has Role",
		filters={"role": "Sparsh Reviewer", "parenttype": "User"},
		fields=["parent as user"],
	)
	users = sorted({r.user for r in rows})
	enabled = [u for u in users if frappe.db.get_value("User", u, "enabled")]
	return {"with_the_reviewer_role": users, "enabled": enabled}


def _rules_holding_an_answer():
	"""Rules carrying the programme owner's wording but not yet Validated.

	This is the gap between a decision being *made* and a decision being *in force*. A
	rule may only be Validated when the record behind it is complete: who approved the
	wording, the document that says so, and the date it took effect.

	The reasons below used to say "a named owner", from an earlier version of the guard
	that required a `User`. That requirement was wrong and is gone -- it made approving a
	rule clinically depend on holding a Frappe login -- but the reason string outlived
	it, so this report named a blocker that no longer existed.
	"""
	rows = frappe.get_all(
		"Sparsh Source of Truth Rule",
		filters={"status": ("!=", "Superseded")},
		fields=["name", "rule_id", "status", "approved_statement", "approved_by_name",
				"approval_source", "effective_date", "criticality"],
	)
	answered_not_in_force, in_force, no_answer = [], [], []
	for row in rows:
		has_answer = bool((row.approved_statement or "").strip())
		if row.status == "Validated":
			in_force.append(row.rule_id)
		elif has_answer:
			missing = []
			if not (row.approved_by_name or "").strip():
				missing.append("the name of the person who approved it")
			if not (row.approval_source or "").strip():
				missing.append("the document the approval comes from")
			if not row.effective_date:
				missing.append("an effective date")
			answered_not_in_force.append(
				{"rule": row.rule_id, "criticality": row.criticality,
				 "missing": missing or ["the status has not been set"]}
			)
		else:
			no_answer.append(row.rule_id)
	return {
		"in_force": sorted(in_force),
		"answered_but_not_in_force": sorted(answered_not_in_force, key=lambda r: r["rule"]),
		"no_decision_yet": sorted(no_answer),
	}


def _cohorts():
	rows = frappe.get_all(
		"Sparsh Cohort", fields=["name", "title", "status", "pathway"]
	)
	out = []
	for row in rows:
		members = frappe.db.count(
			"Sparsh Cohort Member", {"parent": row.name, "parenttype": "Sparsh Cohort"}
		)
		pathway_status = (
			frappe.db.get_value("Sparsh Pathway", row.pathway, "status") if row.pathway else None
		)
		out.append({
			"cohort": row.name,
			"status": row.status,
			"pathway": row.pathway,
			"pathway_status": pathway_status,
			"members": members,
			"within_suggested_size": SUGGESTED_COHORT_MIN <= members <= SUGGESTED_COHORT_MAX,
		})
	return out


@frappe.whitelist()
def status():
	"""What stands between here and a first pilot session."""
	require_reviewer()

	from sparsh_los import seed

	readiness = seed.programme_readiness()
	pathway = _pilot_pathway()
	reviewers = _reviewers()
	rules = _rules_holding_an_answer()
	cohorts = _cohorts()

	usable_cohorts = [
		c for c in cohorts
		if c["status"] == "Active" and c["pathway"] and c["pathway_status"] == "Active"
		and c["members"]
	]

	blocking = []
	if not pathway:
		blocking.append("The pilot pathway has not been seeded")
	elif pathway.status != "Active":
		blocking.append(
			f"The pilot pathway is {pathway.status}. Activating it is the programme "
			f"owner's act -- the engine will not hand out work from a pathway that is not Active"
		)
	if not reviewers["enabled"]:
		blocking.append("No enabled account holds the reviewer role, so no attempt can be judged")
	if not usable_cohorts:
		blocking.append(
			"No Active cohort has members on an Active pathway, so no learner is assigned anything"
		)
	if not readiness.get("can_pilot_with_human_review"):
		blocking.append(
			"Readiness refuses a human-reviewed pilot: " + "; ".join(readiness.get("blocking", []))
		)

	return {
		"can_begin": not blocking,
		"blocking": blocking,
		"pilot_pathway": pathway,
		"cohorts": cohorts,
		"suggested_cohort_size": f"{SUGGESTED_COHORT_MIN}-{SUGGESTED_COHORT_MAX}",
		"reviewers": reviewers,
		"rules": rules,
		# Repeated here because a pilot can be *ready* and still be unable to score
		# anything automatically, and the two are constantly confused.
		"nothing_scores_automatically_until": (
			"a rule is Validated. Rules carrying the programme owner's wording are listed "
			"under rules.answered_but_not_in_force with what each still needs."
		),
		"readiness": {
			"can_pilot_with_human_review": readiness.get("can_pilot_with_human_review"),
			"blocking": readiness.get("blocking"),
		},
	}


@frappe.whitelist()
def prepare(volunteers, reviewers, cohort_id="PILOT-1", activate=0):
	"""Stand the pilot up from a roster, or say precisely why it cannot.

	Everything Phase 6 needs is built; what is missing is names. This is the one command
	that turns a roster into a running pilot: it enrols the volunteers, gives the
	reviewers the reviewer role, seeds the pathway if it is absent, and puts the cohort
	on it.

	It does **not** activate the pathway unless asked. `next_in_pathway` refuses work
	from a pathway that is not Active, so activation is the act that actually starts the
	pilot, and that belongs to the programme owner rather than to whoever runs a script.
	Pass `activate=1` only when he has said to begin.

	Accounts are created only for addresses that do not already exist, and never with a
	password: they are enrolment records, and the people sign in through whatever the
	site already uses. If an address is wrong, the wrong person is enrolled, so the
	roster is echoed back in the result for checking.
	"""
	require_reviewer()

	volunteers = frappe.parse_json(volunteers) if isinstance(volunteers, str) else volunteers
	reviewers = frappe.parse_json(reviewers) if isinstance(reviewers, str) else reviewers

	volunteers = [str(v).strip() for v in (volunteers or []) if str(v).strip()]
	reviewers = [str(r).strip() for r in (reviewers or []) if str(r).strip()]

	refusals = []
	if not volunteers:
		refusals.append("no volunteers were named")
	if not reviewers:
		refusals.append("no reviewer was named, so no attempt could be judged")
	overlap = sorted(set(volunteers) & set(reviewers))
	if overlap:
		# Not fatal in the engine -- every self-judgement guard keys on the subject, not
		# the role -- but on a pilot of eight it means somebody is likely to be the only
		# available reviewer for their own work, and the queue will simply stall.
		refusals.append(
			f"these are named as both volunteer and reviewer: {', '.join(overlap)}"
		)
	if refusals:
		frappe.throw(_("The pilot cannot be prepared: {0}.").format("; ".join(refusals)))

	from sparsh_los import seed

	# The pathway first: a cohort pointing at a pathway that does not exist assigns
	# nobody anything, and the cohort would look configured.
	pathway = seed.PILOT_PATHWAY
	pathway_result = seed.load_pilot_pathway()

	created_accounts, existing_accounts = [], []
	for email in volunteers + reviewers:
		if frappe.db.exists("User", email):
			existing_accounts.append(email)
			continue
		user = frappe.new_doc("User")
		user.email = email
		user.first_name = email.split("@")[0]
		user.enabled = 1
		user.user_type = "System User"
		user.insert(ignore_permissions=True)
		created_accounts.append(email)

	for email in volunteers:
		_grant(email, "Sparsh Learner")
	for email in reviewers:
		_grant(email, "Sparsh Reviewer")

	cohort = _ensure_cohort(cohort_id, pathway, volunteers)

	if frappe.utils.cint(activate):
		frappe.db.set_value("Sparsh Pathway", pathway, "status", "Active")
	frappe.db.commit()

	return {
		"cohort": cohort,
		"pathway": pathway,
		"pathway_steps": pathway_result.get("steps"),
		"pathway_status": frappe.db.get_value("Sparsh Pathway", pathway, "status"),
		"volunteers": volunteers,
		"reviewers": reviewers,
		"accounts_created": created_accounts,
		"accounts_already_present": existing_accounts,
		"cohort_size_against_his_suggestion": (
			f"{len(volunteers)} named; he suggested "
			f"{SUGGESTED_COHORT_MIN}-{SUGGESTED_COHORT_MAX}"
		),
		# The verdict, recomputed from what now exists rather than assumed from what
		# this function just did.
		"status": status(),
	}


def _grant(email, role):
	user = frappe.get_doc("User", email)
	if role not in [row.role for row in user.roles]:
		user.append("roles", {"role": role})
		user.save(ignore_permissions=True)


def _ensure_cohort(cohort_id, pathway, volunteers):
	"""One cohort on the pilot pathway, holding exactly the named volunteers."""
	name = frappe.db.get_value("Sparsh Cohort", {"cohort_id": cohort_id}, "name")
	doc = frappe.get_doc("Sparsh Cohort", name) if name else frappe.new_doc("Sparsh Cohort")
	if not name:
		doc.cohort_id = cohort_id
		doc.title = "Pilot cohort"
	doc.pathway = pathway
	doc.status = "Active"
	doc.started_on = doc.started_on or frappe.utils.today()

	held = {row.learner for row in (doc.members or [])}
	for email in volunteers:
		if email not in held:
			doc.append("members", {"learner": email})
	doc.save(ignore_permissions=True)
	return doc.name
