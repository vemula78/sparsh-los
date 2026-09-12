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
		# Draft, always. A rule becomes Validated when a person says so.
		rule.status = "Draft"
		rule.insert(ignore_permissions=True)
		created.append(rule.name)

	frappe.db.commit()
	return created, skipped


@frappe.whitelist()
def matrix_status():
	"""How much of the matrix is still waiting on the programme owner."""
	rows = frappe.get_all(
		"Sparsh Source of Truth Rule",
		filters={"version": 1},
		fields=["rule_id", "status", "criticality", "automation_status"],
	)

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
		activity.expected_evidence = case["engine"]
		activity.version = 1
		# Human review, always. No rule behind these cases is validated yet.
		activity.evaluation_mode = "Human review"
		activity.insert(ignore_permissions=True)
		created.append(activity.name)

	frappe.db.commit()
	return created, skipped


@frappe.whitelist()
def case_pack_status():
	"""What the case pack looks like in the system, and what it still needs."""
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
