# Copyright (c) 2026, SSSIHMS and contributors
# For license information, please see license.txt

"""Give a pathway with no status at all the Draft it should have been created with.

`SSP-PILOT` was seeded on the live site by a build that predated the `status` field, so
it arrived holding NULL. `load_pilot_pathway` could never repair it: it returns early on
`frappe.db.exists`, treating "the pathway is already there" as "the pathway is already
right", so re-seeding reported 9 steps and changed nothing.

NULL was not dangerous -- `next_in_pathway` refuses work from any pathway that is not
Active, so a NULL pathway hands out nothing, which is the safe direction to fail. It was
still wrong: nothing in the engine had said what state the pilot was in.

Only an empty status is filled. A pathway somebody has set to Active is left exactly as
it is: activating the pilot is the programme owner's act, and a patch that could quietly
undo it would be a worse bug than the one being fixed here.
"""

import frappe


def execute():
	meta = frappe.get_meta("Sparsh Pathway")
	if not meta.has_field("status"):
		return

	rows = frappe.get_all("Sparsh Pathway", fields=["name", "status"])
	filled = []
	for row in rows:
		if (row.status or "").strip():
			continue
		frappe.db.set_value("Sparsh Pathway", row.name, "status", "Draft", update_modified=False)
		filled.append(row.name)

	frappe.db.commit()

	print("set_unset_pathway_status_to_draft: %d pathway(s) given the Draft they were "
		  "created without" % len(filled))
	if filled:
		print("  %s" % ", ".join(sorted(filled)))
