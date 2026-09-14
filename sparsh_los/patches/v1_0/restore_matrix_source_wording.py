# Copyright (c) 2026, SSSIHMS and contributors
# For license information, please see license.txt

"""Put the programme owner's own matrix wording onto rules already seeded.

Three of the matrix's columns cannot be stored in our Selects without loss. The matrix
distinguishes "Medium" from "Normal" and "High" from "High-risk"; its automation values
carry the condition under which automation becomes permissible -- "Do not automate
*until validated*", "deterministic *once validated*" -- and a four-option Select drops
that clause entirely. One row loaded stricter than the owner had authorised, with
nothing on the record to show it.

The mapped values stay: the engine reads them. These fields hold what he actually
wrote, so the two documents can be reconciled by eye.

`load_matrix` leaves existing rules alone unless forced, so the corrected loader reaches
new sites only. This reaches the ones already seeded. Nothing here touches
`rule_statement`, `status` or any value the engine acts on, and `approved_statement` is
only ever written from a non-empty Programme Owner Decision -- which is empty on every
row today.
"""

import json
import pathlib

import frappe

MATRIX = pathlib.Path(frappe.get_app_path("sparsh_los")) / "data" / "source_of_truth_matrix.json"

FIELDS = ("source_criticality", "source_status", "source_automation_status")


def execute():
	meta = frappe.get_meta("Sparsh Source of Truth Rule")
	if not all(meta.has_field(f) for f in FIELDS):
		return

	if not MATRIX.exists():
		return

	rows = json.loads(MATRIX.read_text())
	updated = 0
	absent = []

	for row in rows:
		name = frappe.db.get_value(
			"Sparsh Source of Truth Rule", {"rule_id": row["rule_id"], "version": 1}, "name"
		)
		if not name:
			absent.append(row["rule_id"])
			continue

		stored = frappe.db.get_value(
			"Sparsh Source of Truth Rule", name, list(FIELDS) + ["approved_statement"], as_dict=True
		)

		values = {f: row.get(f) for f in FIELDS if not stored.get(f) and row.get(f)}

		# Only from a filled Programme Owner Decision, and never over an existing answer:
		# his wording is the one thing here a migration must not touch twice.
		if row.get("approved_statement") and not (stored.approved_statement or "").strip():
			values["approved_statement"] = row["approved_statement"]

		if values:
			frappe.db.set_value("Sparsh Source of Truth Rule", name, values, update_modified=False)
			updated += 1

	frappe.db.commit()

	# Rule ids are programme content, not patient data.
	print("restore_matrix_source_wording: %d rule(s) updated, %d matrix row(s) not seeded here"
		  % (updated, len(absent)))
	if absent:
		print("  not present on this site: %s" % ", ".join(absent))
