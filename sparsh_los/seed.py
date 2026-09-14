# Copyright (c) 2026, SSSIHMS and contributors
# For license information, please see license.txt

"""Load the programme's Source-of-Truth Matrix as Draft rules.

Every row arrives as Draft. Not one of them is a validated rule, and the engine
treats Draft as unusable for scoring: putting the matrix in the system is how the
programme owner fills it in, not a shortcut to having it filled in.

Nothing here invents a rule. The candidate statements come from the programme's own
matrix, and any row whose statement is empty is loaded empty rather than guessed at.
"""

import json
import pathlib

import frappe

DATA = pathlib.Path(__file__).parent / "data" / "source_of_truth_matrix.json"
CASES = pathlib.Path(__file__).parent / "data" / "starter_case_pack.json"

# The three competencies the programme chose to stress-test different parts of the
# engine. Named here because the case pack refers to them in prose.
MVP_COMPETENCIES = {
	"Risk stratification": ("SSP-RISK", "Risk judgement"),
	"Pledge co-creation": ("SSP-PLEDGE", "Coaching"),
	"Pledge co-creation / contextual coaching": ("SSP-PLEDGE", "Coaching"),
	"Contextual coaching": ("SSP-PLEDGE", "Coaching"),
	"Scope boundary / escalation": ("SSP-SCOPE", "Safety"),
	"Documentation": ("SSP-DOC", "Documentation"),
	"Follow-up coaching and documentation": ("SSP-DOC", "Documentation"),
}


def _rows():
	return json.loads(DATA.read_text())


def load_matrix(force=False):
	"""Create a Draft rule per matrix row. Idempotent: existing rules are left alone.

	Returns (created, skipped) so a re-run reports what it actually did.
	"""
	created, skipped = [], []

	for row in _rows():
		existing = frappe.db.exists(
			"Sparsh Source of Truth Rule", {"rule_id": row["rule_id"], "version": 1}
		)
		if existing and not force:
			skipped.append(row["rule_id"])
			continue

		if existing:
			frappe.delete_doc(
				"Sparsh Source of Truth Rule", existing, force=True, ignore_permissions=True
			)

		rule = frappe.new_doc("Sparsh Source of Truth Rule")
		rule.rule_id = row["rule_id"]
		rule.version = 1
		rule.rule_statement = row["rule_statement"]
		rule.applies_to = row["applies_to"]
		rule.source = row["source"]
		rule.criticality = row["criticality"]
		rule.automation_status = row["automation_status"]
		rule.notes = row["notes"]
		# The programme owner's own words for the same three columns, kept verbatim
		# beside the values we had to map onto our Selects. The mapping is lossy in both
		# directions: the matrix distinguishes "Medium" from "Normal" and "High" from
		# "High-risk", and its automation values carry the condition under which
		# automation becomes permissible -- "Do not automate *until validated*",
		# "deterministic *once validated*" -- which our four-option Select cannot hold.
		# Without these the loaded rules cannot be reconciled against his spreadsheet by
		# eye, and one row loaded stricter than he had authorised with nothing to show it.
		rule.source_criticality = row.get("source_criticality")
		rule.source_status = row.get("source_status")
		rule.source_automation_status = row.get("source_automation_status")
		# His approved wording, if the matrix's Programme Owner Decision column is filled.
		# Empty on every row today; the candidate in `rule_statement` is not an answer.
		rule.approved_statement = row.get("approved_statement")
		# Draft, always. A rule becomes Validated when a person says so.
		rule.status = "Draft"
		rule.insert(ignore_permissions=True)
		created.append(rule.name)

	frappe.db.commit()
	return created, skipped


# His answer to "which rules govern which competency", transcribed from the decisions
# document. Structural, not clinical: it says which rule the engine must hold a
# competency to, and he wrote it out per competency. Two of the items he lists have no
# matrix row of their own -- "Approved pledge co-creation principles" and "Preservation
# of S episode history" -- and are recorded in COMPETENCY_RULES_UNMATCHED rather than
# invented as rules.
COMPETENCY_RULES = {
	"SSP-DOC": ["IMMUTABLE-BASELINE", "MISSING-DATA", "PARTIAL-ACHIEVEMENT"],
	"SSP-PLEDGE": ["S-MODIFIER", "70-CONFIDENCE-RULE"],
	"SSP-RISK": [
		"RISK-LEVEL-1-DEFINITION",
		"RISK-LEVEL-2-DEFINITION",
		"RISK-LEVEL-3-DEFINITION",
		"RISK-LEVEL-4-DEFINITION",
	],
	"SSP-SCOPE": [
		"RED-FLAG-SYMPTOMS-AND-REFERRAL",
		"BP-HANDLING-AND-ESCALATION",
		"HYPOGLYCAEMIA-RESPONSE-SCOPE",
		"MEDICATION-QUESTIONS",
	],
}

# Named in his answer, with no matrix row to link. Reported, never invented.
COMPETENCY_RULES_UNMATCHED = {
	"SSP-DOC": ["Preservation of S episode history"],
	"SSP-PLEDGE": ["Approved pledge co-creation principles",
				   "Relevant topic-specific guidance only when applicable to that case"],
}


# The programme owner's assessment rule for the scope competency, transcribed verbatim
# from his answer to "what must a volunteer demonstrate". It is the only competency he
# has calibrated; the others keep the engine's uncalibrated default of two distinct
# activities, which §16 says must eventually be set from observed performance rather
# than assumed.
COMPETENCY_THRESHOLDS = {
	"SSP-SCOPE": {
		"assessment_cases": 5,
		"unaided_passes_required": 4,
		"all_safety_decisions_must_be_correct": 1,
		"threshold_source": (
			"Programme owner, 13-Sep-2026: \"5 varied cases covering medication, symptoms, "
			"red flags, lifestyle coaching and an ambiguous boundary; all safety-critical "
			"decisions must be correct; at least 4 of 5 should be completed unaided; no "
			"critical boundary violation is acceptable.\" During the first pilot all five "
			"cases remain human-reviewed."
		),
	},
}


@frappe.whitelist()
def load_competency_thresholds():
	"""Record the programme owner's assessment rules on the competencies they govern.

	`activities_for_mastery` is deliberately left alone here. He specified how many cases
	must be *assessed* and how many unaided, which is the certification rule; how many
	distinct activities constitute Mastered is a different question and he has not
	answered it. Setting one from the other would be inference dressed as his decision.
	"""
	applied, missing = [], []

	for competency_id, values in COMPETENCY_THRESHOLDS.items():
		name = frappe.db.get_value("Sparsh Competency", {"competency_id": competency_id}, "name")
		if not name:
			missing.append(competency_id)
			continue
		frappe.db.set_value("Sparsh Competency", name, values, update_modified=False)
		applied.append(competency_id)

	frappe.db.commit()

	uncalibrated = [
		row.competency_id
		for row in frappe.get_all("Sparsh Competency", fields=["name", "competency_id"])
		if row.competency_id not in COMPETENCY_THRESHOLDS
	]
	return {
		"thresholds_applied": applied,
		"competency_not_found": missing,
		# Named rather than left implicit: a default that nobody chose still decides
		# whether a volunteer is called competent.
		"still_on_the_uncalibrated_default": uncalibrated,
		"default_in_use": "2 distinct activities for Mastered",
	}


@frappe.whitelist()
def link_competency_rules():
	"""Attach the rules the programme owner says govern each competency.

	Until now every competency had no rule linked at all, which is the one configuration
	that walks through the validation gate: `_rule_is_validated` returns True when
	nothing is linked, so a competency governed by nothing scored as freely as one
	governed by an approved rule. His mapping closes that.

	Links the current, non-superseded version of each rule. Re-running is safe: a link
	that already exists is left alone rather than duplicated.
	"""
	linked, missing_rule, missing_competency, already = [], [], [], []

	for competency_id, rule_ids in COMPETENCY_RULES.items():
		competency = frappe.db.get_value("Sparsh Competency", {"competency_id": competency_id}, "name")
		if not competency:
			missing_competency.append(competency_id)
			continue

		doc = frappe.get_doc("Sparsh Competency", competency)
		existing = {row.rule for row in (doc.linked_rules or [])}

		for rule_id in rule_ids:
			rule = frappe.db.get_value(
				"Sparsh Source of Truth Rule",
				{"rule_id": rule_id, "status": ("!=", "Superseded")},
				"name",
				order_by="version desc",
			)
			if not rule:
				missing_rule.append(f"{competency_id}:{rule_id}")
				continue
			if rule in existing:
				already.append(f"{competency_id}:{rule_id}")
				continue
			doc.append("linked_rules", {"rule": rule})
			linked.append(f"{competency_id}:{rule_id}")

		doc.save(ignore_permissions=True)

	frappe.db.commit()

	return {
		"linked": linked,
		"already_linked": already,
		"rule_not_found": missing_rule,
		"competency_not_found": missing_competency,
		# He names these as governing rules; the matrix has no row for them, so there is
		# nothing to link and nothing may be invented in their place.
		"named_but_not_in_the_matrix": COMPETENCY_RULES_UNMATCHED,
	}


# The governance record the approvals come from lives beside the matrix, not here: a
# person's name and a programme's document title are content, and no module in this
# engine may carry the programme's name. `_approval()` reads it.
APPROVAL = pathlib.Path(__file__).parent / "data" / "source_of_truth_approval.json"


def _approval():
	"""Who approved the matrix rows, and the document that says so."""
	if not APPROVAL.exists():
		return {}
	return json.loads(APPROVAL.read_text())


@frappe.whitelist()
def load_matrix_decisions():
	"""Apply the programme owner's completed matrix to rules already seeded.

	He returned the matrix with ten Programme Owner Decisions filled and dated. Six of
	those rewrote the candidate statement materially -- Level 4 is now a permanent
	survivor category rather than an open question about acute states, and S is a
	Special *coaching* modifier where our seeded text said "special/surgical", an
	assumption his handover note explicitly retires.

	A material rewrite gets a **new version that supersedes the old one**, never an edit.
	Overwriting would erase what the engine was previously configured to believe, and
	the controller's supersession path also marks every learner judged against the old
	wording Refresh Due -- which is exactly right when the rule they were judged against
	has changed.

	Where his matrix records a decision of "Validated", that decision is applied: the
	status is set through the document, so the controller's check that an approver, a
	source and a date are all present still has to pass. This is not the script deciding
	anything -- it is the script transcribing a decision he has already made and signed.
	Rows his matrix leaves undecided stay Draft and are reported, never assumed.
	"""
	approval = _approval()
	applied, superseded, skipped, ready, promoted = [], [], [], [], []

	for row in _rows():
		if not row.get("approved_statement"):
			skipped.append(row["rule_id"])
			continue

		current = frappe.db.get_value(
			"Sparsh Source of Truth Rule",
			{"rule_id": row["rule_id"], "status": ("!=", "Superseded")},
			["name", "version", "rule_statement", "approved_statement", "status"],
			as_dict=True,
			order_by="version desc",
		)
		if not current:
			skipped.append(row["rule_id"])
			continue

		if current.approved_statement:
			# Already carries an answer. Re-running must not rewrite a decision -- but
			# carrying the wording and being Validated are two different things, and they
			# are not always written together. `restore_matrix_source_wording` writes the
			# wording alone; a row that arrived that way would stay Draft forever, because
			# this branch used to `continue` on the wording and never look at the status.
			# Seven rules the programme owner had signed sat inert on the live site that
			# way. Promoting here rewrites no decision: it applies the one he already made.
			if row.get("source_status") == "Validated" and current.status != "Validated":
				# Provenance first, or the controller refuses the transition -- rightly:
				# Validated means a named person approved this wording on a date, in a
				# document somebody can go and read. `restore_matrix_source_wording`
				# writes the wording alone, so on a site seeded that way these three
				# fields are empty and the promotion cannot be allowed to proceed
				# without them. Writing them rewrites no decision: the wording, which is
				# his, is left exactly as it stands.
				frappe.db.set_value(
					"Sparsh Source of Truth Rule", current.name,
					{
						"effective_date": row.get("effective_date"),
						"source_status": row.get("source_status"),
						"source_criticality": row.get("source_criticality"),
						"source_automation_status": row.get("source_automation_status"),
						"approved_by_name": approval.get("approved_by_name"),
						"approval_source": approval.get("approval_source"),
					},
					update_modified=False,
				)
				doc = frappe.get_doc("Sparsh Source of Truth Rule", current.name)
				doc.status = "Validated"
				doc.save(ignore_permissions=True)
				promoted.append(row["rule_id"])
			else:
				ready.append(row["rule_id"])
			continue

		if row.get("supersedes_previous"):
			successor = frappe.new_doc("Sparsh Source of Truth Rule")
			successor.rule_id = row["rule_id"]
			successor.version = (current.version or 1) + 1
			successor.rule_statement = row["rule_statement"]
			successor.approved_statement = row["approved_statement"]
			successor.applies_to = row["applies_to"]
			successor.source = row["source"]
			successor.criticality = row["criticality"]
			successor.automation_status = row["automation_status"]
			successor.notes = row["notes"]
			successor.source_criticality = row.get("source_criticality")
			successor.source_status = row.get("source_status")
			successor.source_automation_status = row.get("source_automation_status")
			successor.effective_date = row.get("effective_date")
			successor.approved_by_name = approval.get("approved_by_name")
			successor.approval_source = approval.get("approval_source")
			successor.supersedes = current.name
			# His own status, not ours. "Validated" in his matrix is the sign-off; the
			# controller refuses it unless the approver, the source and the date are all
			# present, which is why they are set immediately above.
			successor.status = "Validated" if row.get("source_status") == "Validated" else "Draft"
			successor.insert(ignore_permissions=True)
			superseded.append(f"{row['rule_id']} v{current.version} -> v{successor.version}")
		else:
			frappe.db.set_value(
				"Sparsh Source of Truth Rule", current.name,
				{
					"approved_statement": row["approved_statement"],
					"effective_date": row.get("effective_date"),
					"source_status": row.get("source_status"),
					"source_criticality": row.get("source_criticality"),
					"source_automation_status": row.get("source_automation_status"),
					"approved_by_name": approval.get("approved_by_name"),
					"approval_source": approval.get("approval_source"),
				},
				update_modified=False,
			)
			# Through the document, not db.set_value: the status transition must pass the
			# controller's check that an approver, a source and a date are all present.
			# Writing the status with the fields above would walk straight past it.
			if row.get("source_status") == "Validated":
				doc = frappe.get_doc("Sparsh Source of Truth Rule", current.name)
				doc.status = "Validated"
				doc.save(ignore_permissions=True)
			applied.append(row["rule_id"])

	frappe.db.commit()

	return {
		"decisions_applied": applied,
		"superseded_with_new_version": superseded,
		"already_carried_a_decision": ready,
		"promoted_to_validated": promoted,
		"no_decision_in_the_matrix": skipped,
		# The one thing standing between these and Validated.
		"approved_by": approval.get("approved_by_name"),
		"approval_source": approval.get("approval_source"),
	}


@frappe.whitelist()
def matrix_status():
	"""How much of the matrix is still waiting on the programme owner.

	Reviewer-gated: the rule inventory is programme internals, and gating only the
	`programme_readiness` wrapper left the same data callable directly.
	"""
	from sparsh_los.permissions import require_reviewer

	require_reviewer()
	return _matrix_status()


def _matrix_status():
	# The current version of each rule lineage, not version 1. A v1 superseded by a
	# validated v2 was still reported as unvalidated and blocking.
	# Only the matrix's own rules. Widening this to every rule swept up anything else
	# the site happened to hold and reported it as programme status.
	matrix_ids = [row["rule_id"] for row in _rows()]
	all_rows = frappe.get_all(
		"Sparsh Source of Truth Rule",
		filters={"rule_id": ("in", matrix_ids)},
		fields=["name", "rule_id", "version", "status", "criticality", "automation_status"],
		order_by="rule_id asc, version asc",
	)
	current = {}
	for row in all_rows:
		if row.status == "Superseded":
			continue
		existing = current.get(row.rule_id)
		if not existing or (row.version or 0) > (existing.version or 0):
			current[row.rule_id] = row
	rows = list(current.values())

	by_status = {}
	for row in rows:
		by_status[row.status] = by_status.get(row.status, 0) + 1

	unvalidated_safety = [
		r.rule_id
		for r in rows
		if r.criticality == "Safety-critical" and r.status != "Validated"
	]

	return {
		"total": len(rows),
		"by_status": by_status,
		"unvalidated_safety_critical": unvalidated_safety,
		"ready_to_automate": [
			r.rule_id
			for r in rows
			if r.status == "Validated" and r.automation_status == "Safe as fixed logic"
		],
	}


def _competency_for(label):
	return MVP_COMPETENCIES.get(label.strip())


def load_case_pack():
	"""Load the Starter Case Pack as activities awaiting human review.

	Every case arrives in Human review mode with no expected response. That is not a
	shortcut: not one of the rules these cases turn on has been validated, so the
	engine must not auto-score any of them. A reviewer judges each attempt until the
	programme owner locks the rules, at which point the activities can be given
	deterministic answers.

	Returns (created, skipped).
	"""
	created, skipped = [], []
	domains, competencies = set(), {}

	for case in json.loads(CASES.read_text()):
		mapping = _competency_for(case["competency"])
		if not mapping:
			skipped.append(case["code"])
			continue

		competency_id, domain_name = mapping
		domains.add(domain_name)
		competencies[competency_id] = (domain_name, case["competency"])

	for domain_name in sorted(domains):
		domain_id = domain_name.upper().replace(" ", "-")
		if not frappe.db.exists("Sparsh Competency Domain", domain_id):
			doc = frappe.new_doc("Sparsh Competency Domain")
			doc.domain_id = domain_id
			doc.domain_name = domain_name
			doc.insert(ignore_permissions=True)

	for competency_id, (domain_name, label) in competencies.items():
		if frappe.db.exists("Sparsh Competency", competency_id):
			continue
		doc = frappe.new_doc("Sparsh Competency")
		doc.competency_id = competency_id
		doc.competency_name = label
		doc.domain = domain_name.upper().replace(" ", "-")
		doc.insert(ignore_permissions=True)

	for case in json.loads(CASES.read_text()):
		mapping = _competency_for(case["competency"])
		if not mapping:
			continue

		if frappe.db.exists("Sparsh Activity", case["code"]):
			skipped.append(case["code"])
			continue

		activity = frappe.new_doc("Sparsh Activity")
		activity.activity_id = case["code"]
		activity.title = case["title"]
		activity.competency = mapping[0]
		activity.activity_type = "Short case"
		activity.instruction = f"{case['scenario']}\n\n{case['task']}"
		# `expected_evidence` is what the learner must demonstrate, and the case pack
		# states it for no case. It used to be filled with the pack's "suggested engine
		# behaviour" -- developer guidance like "Short case -> decision -> brief
		# reasoning -> graded hint" -- which a reviewer opening the activity reads as the
		# standard they are judging against. Left empty: missing is missing.
		activity.expected_evidence = None
		activity.engine_guidance = case["engine"]
		# Carried across rather than dropped. A reviewer judging this case needs to know
		# the programme has not validated the rule behind it, and what specifically is
		# outstanding -- for SC-06 that is "use programme-approved red-flag/referral
		# rules only", which is the difference between judging a learner and guessing.
		activity.validation_required = case.get("validation")
		activity.source_status = case.get("status")
		activity.version = 1
		# Human review, always. No rule behind these cases is validated yet.
		activity.evaluation_mode = "Human review"
		activity.insert(ignore_permissions=True)
		created.append(activity.name)

	frappe.db.commit()
	return created, skipped


PILOT_PATHWAY = "SSP-PILOT"


@frappe.whitelist()
def load_pilot_pathway():
	"""Order the nine starter cases into one sequence a learner can be put through.

	Until now nothing connected a learner to a Pathway: `practice` iterated competencies
	and asked the orchestrator for something to do in each, which answers "what could
	this person do next?" but not "what is this person being taken through?" -- and §23
	acceptance criterion 1 asks for an *assigned* pathway.

	The order is the case pack's own: SC-01 to SC-09 as the programme wrote them. The
	engine has no basis for a different one, and inventing a pedagogical sequence is a
	programme decision, not a seeding decision.

	Created as **Draft**, deliberately. `next_in_pathway` refuses to hand out work from a
	pathway that is not Active, so the programme owner activating it is the act that
	starts the pilot. Seeding it Active would start the pilot by running a script.

	Every step is mandatory: all nine cases are Human review, so none can be passed over
	by the engine deciding the learner already demonstrated it.
	"""
	if frappe.db.exists("Sparsh Pathway", PILOT_PATHWAY):
		# The real step count, not zero: a caller reading `steps` to confirm the pathway
		# was built got "0 steps" from the idempotent path and could not tell that from
		# an empty pathway.
		return {
			"pathway": PILOT_PATHWAY,
			"created": False,
			"steps": frappe.db.count("Sparsh Pathway Step", {"parent": PILOT_PATHWAY}),
		}

	cases = frappe.get_all(
		"Sparsh Activity",
		filters={"activity_id": ("like", "SC-%")},
		fields=["name", "activity_id", "competency"],
		order_by="activity_id asc",
	)
	if not cases:
		frappe.throw(frappe._("Load the starter case pack before building the pilot pathway"))

	pathway = frappe.new_doc("Sparsh Pathway")
	pathway.pathway_id = PILOT_PATHWAY
	pathway.title = "Starter case pack"
	pathway.target_role = "Volunteer"
	pathway.status = "Draft"
	pathway.description = (
		"The nine starter cases in the order the programme wrote them. Every case is "
		"judged by a person: no rule behind them is validated."
	)
	for index, case in enumerate(cases, start=1):
		pathway.append(
			"steps",
			{
				"step_order": index,
				"activity": case.name,
				"competency": case.competency,
				"is_mandatory": 1,
			},
		)
	pathway.insert(ignore_permissions=True)
	frappe.db.commit()

	return {"pathway": pathway.name, "created": True, "steps": len(cases)}


@frappe.whitelist()
def case_pack_status():
	"""What the case pack looks like in the system, and what it still needs."""
	from sparsh_los.permissions import require_reviewer

	require_reviewer()
	return _case_pack_status()


def _case_pack_status():
	activities = frappe.get_all(
		"Sparsh Activity",
		filters={"activity_id": ("like", "SC-%")},
		fields=["name", "competency", "evaluation_mode", "expected_response"],
	)
	return {
		"loaded": len(activities),
		"awaiting_human_review": len([a for a in activities if a.evaluation_mode == "Human review"]),
		"auto_scored": [a.name for a in activities if a.evaluation_mode == "Deterministic"],
	}


@frappe.whitelist()
def programme_readiness():
	"""What stands between the platform and a pilot, stated plainly.

	Written for the programme owner rather than the developer: every line is
	something a person has to decide, not something left to build.
	"""
	from sparsh_los.permissions import require_reviewer

	require_reviewer()

	matrix = _matrix_status()
	cases = _case_pack_status()

	# Activities that would score themselves against a rule nobody has validated. The
	# runner now refuses these at evaluation time; naming them here is how the
	# programme owner sees the gap rather than discovering it as silent human review.
	auto_scoring_unvalidated = []
	no_rule_but_scoring = []
	for row in frappe.get_all(
		"Sparsh Activity",
		filters={"evaluation_mode": "Deterministic"},
		fields=["name", "competency"],
	):
		# Ask the runner's own question rather than a similar one. This used to require
		# that *every* linked rule be unvalidated, so a competency with one Validated
		# and one Draft rule was reported as fine while the runner silently routed it
		# all to human review -- the exact "discovered as silent human review" this is
		# meant to prevent. And `if rules` short-circuited, so an activity with no rule
		# linked, the one configuration that bypasses the gate, was never named.
		from sparsh_los.runner import _rule_is_validated

		if not _rule_is_validated(row.competency):
			auto_scoring_unvalidated.append(row.name)
		elif not frappe.get_all(
			"Sparsh Competency Rule Link", filters={"parent": row.competency}, limit=1
		):
			no_rule_but_scoring.append(row.name)

	# Scoring is not the only judgement the engine makes without being asked twice.
	# A critical marker produces Evidence that cannot be cancelled -- a permanent block
	# -- and the marker branch sits *after* the rule gate, which `_rule_is_validated`
	# passes when the competency links no rule at all. So a Human-review activity, which
	# this loop never looked at because it filters on Deterministic, still imposes an
	# irreversible block on the authority of a rule nobody validated. The case pack asks
	# for exactly this configuration: a critical-error flag on a case awaiting review.
	critical_without_rule = []
	for row in frappe.get_all(
		"Sparsh Activity", fields=["name", "competency", "critical_markers"]
	):
		if not (row.critical_markers or "").strip():
			continue
		if frappe.get_all(
			"Sparsh Competency Rule Link", filters={"parent": row.competency}, limit=1
		):
			continue
		critical_without_rule.append(row.name)

	competencies = frappe.get_all(
		"Sparsh Competency", fields=["name", "competency_name"], order_by="name asc"
	)
	without_activities = [
		c.name
		for c in competencies
		if not frappe.db.count("Sparsh Activity", {"competency": c.name})
	]
	without_rules = [
		c.name
		for c in competencies
		if not frappe.db.count("Sparsh Competency Rule Link", {"parent": c.name})
	]

	blocking = []
	if matrix["unvalidated_safety_critical"]:
		blocking.append(
			f"{len(matrix['unvalidated_safety_critical'])} safety-critical rules are not validated"
		)
	if cases["awaiting_human_review"]:
		blocking.append(
			f"{cases['awaiting_human_review']} cases can only be judged by a person until their rules are locked"
		)
	if without_rules:
		blocking.append(f"{len(without_rules)} competencies have no rule linked")
	if without_activities:
		blocking.append(f"{len(without_activities)} competencies have no activity")

	return {
		"rules_total": matrix["total"],
		"rules_by_status": matrix["by_status"],
		"rules_ready_to_automate": matrix["ready_to_automate"],
		"unvalidated_safety_critical": matrix["unvalidated_safety_critical"],
		"cases_loaded": cases["loaded"],
		"cases_awaiting_human_review": cases["awaiting_human_review"],
		"competencies_without_activities": without_activities,
		"competencies_without_rules": without_rules,
		"blocking": blocking,
		# A pilot is only safe if nothing can auto-score against an unvalidated rule.
		# This used to report `not without_activities` alone, which answered "does every
		# competency have something to do?" and was then quoted as "a pilot can run
		# now" -- a stronger claim than the data supported.
		# A no-rule Deterministic activity scores itself just as surely as one against a
		# Draft rule -- `_rule_is_validated` returns True when nothing is linked. Naming
		# it below while still reporting the pilot human-review-safe let the same report
		# assert both things at once.
		# And a permanent safety block imposed under no rule is at least as serious as
		# an auto-scored pass, so it weighs on the verdict too rather than only being
		# listed underneath it.
		"can_pilot_with_human_review": (
			(not without_activities)
			and not auto_scoring_unvalidated
			and not no_rule_but_scoring
			and not critical_without_rule
		),
		"auto_scoring_against_unvalidated_rules": auto_scoring_unvalidated,
		# Reported, not blocking: an activity with no rule linked is the deliberate
		# hole in the gate. The programme owner should see which activities sit in it.
		"auto_scoring_with_no_rule_linked": no_rule_but_scoring,
		# Whatever their evaluation mode: the block does not depend on the engine being
		# able to grade the rest of the answer.
		"critical_markers_with_no_rule_linked": critical_without_rule,
	}


@frappe.whitelist()
def governance_view():
	"""Every matrix rule as the engine now holds it, for the governance page.

	The programme owner's review turns on one question the dashboards cannot answer:
	is the wording the engine will judge people against the wording he actually signed?
	So each rule is returned with his approved statement beside the statement we
	originally proposed, and with the provenance that lets `status` be Validated at all
	-- who approved it, which document, and from what date. A rule whose two statements
	differ is the interesting case; a page that showed only the approved one would hide
	what changed.

	Reviewer-gated for the same reason `matrix_status` is: the rule inventory is
	programme internals, and a read model left ungated is a way round the page.

	Nothing is computed here that the engine does not already hold, and nothing is
	invented: rules he named that have no matrix row are reported under their own key
	rather than given wording of our own.
	"""
	from sparsh_los.permissions import require_reviewer

	require_reviewer()

	source = {row["rule_id"]: row for row in _rows()}
	rule_to_competency = {}
	for competency, rule_ids in COMPETENCY_RULES.items():
		for rule_id in rule_ids:
			rule_to_competency.setdefault(rule_id, []).append(competency)

	held = frappe.get_all(
		"Sparsh Source of Truth Rule",
		filters={"rule_id": ("in", list(source))},
		fields=[
			"name", "rule_id", "version", "status", "rule_statement", "approved_statement",
			"applies_to", "source", "criticality", "automation_status", "effective_date",
			"approved_by_name", "approval_source", "notes",
		],
		order_by="rule_id asc, version asc",
	)

	# The current version of each lineage, exactly as `_matrix_status` picks it: a v1
	# superseded by a validated v2 must not be reported as the rule in force.
	current = {}
	superseded = {}
	for row in held:
		if row.status == "Superseded":
			superseded[row.rule_id] = superseded.get(row.rule_id, 0) + 1
			continue
		existing = current.get(row.rule_id)
		if not existing or (row.version or 0) > (existing.version or 0):
			current[row.rule_id] = row

	rules = []
	for rule_id in source:
		row = current.get(rule_id)
		if not row:
			# Named in the matrix and absent from this site. Reported, not skipped: a
			# rule silently missing from the page reads as a rule that does not exist.
			rules.append({
				"rule_id": rule_id,
				"present": False,
				"status": None,
				"competencies": rule_to_competency.get(rule_id, []),
			})
			continue

		approved = (row.approved_statement or "").strip()
		proposed = (row.rule_statement or "").strip()
		rules.append({
			"rule_id": rule_id,
			"present": True,
			"version": row.version,
			"status": row.status,
			"in_force": row.status == "Validated",
			"proposed_statement": proposed,
			"approved_statement": approved,
			"wording_changed": bool(approved) and approved != proposed,
			"applies_to": row.applies_to,
			"source": row.source,
			"criticality": row.criticality,
			"automation_status": row.automation_status,
			"effective_date": row.effective_date,
			"approved_by_name": row.approved_by_name,
			"approval_source": row.approval_source,
			"notes": row.notes,
			"competencies": rule_to_competency.get(rule_id, []),
			"earlier_versions": superseded.get(rule_id, 0),
		})

	rules.sort(key=lambda r: (r.get("status") != "Validated", r["rule_id"]))

	approval = _approval()
	return {
		"rules": rules,
		"counts": {
			"total": len(rules),
			"in_force": len([r for r in rules if r.get("in_force")]),
			"awaiting_decision": len([r for r in rules if r.get("present") and not r.get("in_force")]),
			"not_on_this_site": len([r for r in rules if not r.get("present")]),
		},
		"approved_by": approval.get("approved_by_name"),
		"approval_source": approval.get("approval_source"),
		# Named by the programme owner as governing, with no row in the matrix to carry
		# them. They are listed so he can supply the wording; they are never invented.
		"named_but_not_in_the_matrix": COMPETENCY_RULES_UNMATCHED,
	}
