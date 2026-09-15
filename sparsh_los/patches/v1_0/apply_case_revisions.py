"""Bring already-seeded activities up to the case pack's current revision.

`load_case_pack` is idempotent by design -- it skips a case that already exists -- so
a revision to the pack reaches a new site and never an existing one. SC-02 was revised
on the programme owner's instruction of 15-Sep-2026 after the case had been seeded
everywhere, which is precisely the gap.

Only the wording and the version move. `source_status` is deliberately untouched: it
records what the programme said about the case, and a revision is not an approval.
"""

import json

import frappe

from sparsh_los.seed import CASES, _case_text


def execute():
	for case in json.loads(CASES.read_text()):
		if not case.get("revision"):
			continue

		name = case["code"]
		if not frappe.db.exists("Sparsh Activity", name):
			continue

		text = _case_text(case)
		instruction = f"{text['scenario']}\n\n{text['task']}"
		current = frappe.db.get_value(
			"Sparsh Activity", name, ["instruction", "version"], as_dict=True
		)
		if current.instruction == instruction and (current.version or 1) >= text["version"]:
			continue

		# db_set on the document, not a bare SQL update: the activity's own controller
		# rejects wording that carries a patient identifier, and a revision arriving
		# through a patch must clear the same gate as one arriving through the form.
		doc = frappe.get_doc("Sparsh Activity", name)
		doc.instruction = instruction
		doc.validation_required = text.get("validation")
		doc.version = text["version"]
		doc.save(ignore_permissions=True)

		frappe.logger().info(
			f"sparsh_los: {name} updated to revision v{text['version']}"
		)
