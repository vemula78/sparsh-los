# Copyright (c) 2026, SSSIHMS and contributors
# For license information, please see license.txt

"""Put the case pack's own text back where it belongs on already-seeded activities.

`load_case_pack` used to store the pack's *suggested engine behaviour* -- developer
guidance such as "Short case -> decision -> brief reasoning -> graded hint" -- in
`expected_evidence`, which is the field a reviewer opens to see what the learner was
supposed to demonstrate. A reviewer judging a pilot attempt would have been reading
build instructions as the standard. The pack states no expected evidence for any case,
so the field is cleared rather than filled: missing is missing.

The pack's own `Current status` and `Programme validation needed` were dropped entirely
on load. For SC-06 the latter reads "Use programme-approved red-flag/referral rules
only" -- the difference between a reviewer judging a learner and a reviewer guessing.

`load_case_pack` skips activities that already exist, so the corrected loader reaches
new sites only. This patch reaches the ones already seeded.

Conservative by design: `expected_evidence` is cleared only where it still holds the
pack's engine text verbatim. If somebody has since authored real expected evidence, it
is left alone and reported, because overwriting authored content is not a migration's
decision to make.
"""

import json
import pathlib

import frappe

CASES = pathlib.Path(frappe.get_app_path("sparsh_los")) / "data" / "starter_case_pack.json"


def execute():
	meta = frappe.get_meta("Sparsh Activity")
	if not all(meta.has_field(f) for f in ("engine_guidance", "validation_required", "source_status")):
		return

	if not CASES.exists():
		return

	cases = json.loads(CASES.read_text())
	corrected = 0
	authored = []
	missing = []

	for case in cases:
		name = frappe.db.get_value("Sparsh Activity", {"activity_id": case["code"]}, "name")
		if not name:
			missing.append(case["code"])
			continue

		row = frappe.db.get_value(
			"Sparsh Activity", name,
			["expected_evidence", "engine_guidance", "validation_required", "source_status"],
			as_dict=True,
		)

		values = {}
		if not row.engine_guidance:
			values["engine_guidance"] = case["engine"]
		if not row.validation_required and case.get("validation"):
			values["validation_required"] = case["validation"]
		if not row.source_status and case.get("status"):
			values["source_status"] = case["status"]

		if (row.expected_evidence or "").strip() == case["engine"].strip():
			values["expected_evidence"] = None
		elif (row.expected_evidence or "").strip():
			authored.append(case["code"])

		if values:
			# update_modified stays False: this corrects where our own loader put text,
			# it is not an authoring change by a person.
			frappe.db.set_value("Sparsh Activity", name, values, update_modified=False)
			corrected += 1

	frappe.db.commit()

	# Case codes are programme content, not patient data, so naming them is safe.
	print("restore_case_pack_provenance: %d case(s) corrected, %d not seeded on this site"
		  % (corrected, len(missing)))
	if authored:
		print("  expected_evidence left alone (authored since seeding): %s" % ", ".join(authored))
	if missing:
		print("  not present on this site: %s" % ", ".join(missing))
