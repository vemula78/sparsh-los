# Copyright (c) 2026, SSSIHMS and contributors
# For license information, please see license.txt

"""Mark existing ledger rows whose cost was recorded before the flags existed.

`actual_cost_recorded` and `estimated_cost_recorded` were added after the DocType
shipped, and a new Check defaults to 0 — so every row written before them would be
reclassified as "nothing known" and its stored cost dropped out of `spend()`'s totals.

The backfill is necessarily a guess for exactly one case, and it guesses the way that
loses the least: a stored cost greater than zero was certainly recorded, so the flag is
set. A stored 0.0 is ambiguous in old rows — the flag exists precisely because the
number cannot say — and is left unset, which reports it as unknown rather than
asserting a billed nil nobody recorded.

No rows exist today; this is written so the gap cannot reach a site that has any.
"""

import frappe


def execute():
	if not frappe.db.table_exists("Sparsh Model Interaction"):
		return

	for field in ("actual_cost", "estimated_cost"):
		flag = f"{field}_recorded"
		if not frappe.db.has_column("Sparsh Model Interaction", flag):
			continue

		frappe.db.sql(
			f"""
			update `tabSparsh Model Interaction`
			set `{flag}` = 1
			where `{field}` > 0 and `{flag}` = 0
			"""
		)

	# No commit here: patch_handler commits after each patch, and committing inside one
	# lands before the Patch Log row is written -- so an interrupt in that window
	# re-runs the patch. Harmless while it stays idempotent, and needless either way.
