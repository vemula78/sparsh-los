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
