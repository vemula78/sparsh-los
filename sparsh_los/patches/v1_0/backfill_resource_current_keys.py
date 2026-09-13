# Copyright (c) 2026, SSSIHMS and contributors
# For license information, please see license.txt

"""Give existing Current resources the key the unique index is built on.

`current_key` was added with the index that enforces one Current version per
`resource_id`. On a site that already holds resources, every row predates the column
and so holds NULL -- and NULLs do not collide, so the index installs cleanly over a
table that may already contain two Current versions of the same resource. The
constraint then reads as enforced while enforcing nothing for exactly the rows that
existed before it.

The same is true of rows whose key was cleared by the bug fixed in `3b8c27b`, where
an ordinary edit to a Current resource dropped the key and nothing restored it.

Duplicates are not resolved here. Picking which of two Current versions is the real
one is a programme decision, not a migration's: the patch reports them and leaves both
NULL, so the index still installs and the pair stays visible rather than one being
silently demoted.
"""

import frappe


def execute():
	if not frappe.db.has_column("Sparsh Learning Resource", "current_key"):
		return

	rows = frappe.db.sql(
		"""select name, resource_id from `tabSparsh Learning Resource`
		   where status = 'Current' and (current_key is null or current_key = '')""",
		as_dict=True,
	)
	if not rows:
		return

	by_resource = {}
	for row in rows:
		by_resource.setdefault(row.resource_id, []).append(row.name)

	claimed = 0
	contested = {}
	for resource_id, names in by_resource.items():
		# A row that is already keyed holds the claim; an unkeyed sibling is a duplicate
		# whichever way round it happened.
		holder = frappe.db.get_value(
			"Sparsh Learning Resource",
			{"resource_id": resource_id, "current_key": resource_id},
			"name",
		)
		if holder or len(names) > 1:
			contested[resource_id] = sorted(names) + ([holder] if holder else [])
			continue
		frappe.db.set_value(
			"Sparsh Learning Resource", names[0], "current_key", resource_id,
			update_modified=False,
		)
		claimed += 1

	frappe.db.commit()

	# Resource ids are programme content, not patient data, so naming them is safe and
	# is the only way the programme owner can act on the report.
	print("backfill_resource_current_keys: %d row(s) examined, %d key(s) claimed, "
		  "%d resource(s) with more than one Current version left unkeyed"
		  % (len(rows), claimed, len(contested)))
	for resource_id, names in sorted(contested.items()):
		print("  contested: %s -> %s" % (resource_id, ", ".join(n for n in names if n)))
