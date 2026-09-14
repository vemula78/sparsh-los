# Copyright (c) 2026, SSSIHMS and contributors
# For license information, please see license.txt

"""Clear the mastery threshold on competencies nobody actually calibrated.

`activities_for_mastery` carried a DocType default of 2. Every competency therefore
arrived holding a number, and a stored 2 is indistinguishable from a 2 somebody chose --
which is precisely what §16 warns against, and it made `calibration.observed` report
every competency as calibrated when none was.

Only rows holding the default *and* carrying no `threshold_source` are cleared: a source
means a person recorded why, and that is a decision, not a default. A competency set to
2 deliberately and without a source cannot be told apart from one that drifted there, so
it is reported rather than guessed at.
"""

import frappe

DEFAULTED = 2


def execute():
	meta = frappe.get_meta("Sparsh Competency")
	if not meta.has_field("activities_for_mastery"):
		return

	rows = frappe.get_all(
		"Sparsh Competency",
		fields=["name", "activities_for_mastery", "threshold_source"],
	)
	cleared, kept = [], []
	for row in rows:
		if (row.activities_for_mastery or 0) != DEFAULTED:
			continue
		if (row.threshold_source or "").strip():
			kept.append(row.name)
			continue
		# Zero, not NULL. Frappe creates an Int column as NOT NULL DEFAULT 0 on this
		# bench, so the field cannot hold "unset" -- the same limit that made
		# `expected_value` a Data field rather than a Float. Zero is therefore the
		# sentinel for uncalibrated, and `derive_state` already reads anything below 1
		# as "nobody has set this".
		frappe.db.set_value(
			"Sparsh Competency", row.name, "activities_for_mastery", 0,
			update_modified=False,
		)
		cleared.append(row.name)

	frappe.db.commit()

	print("clear_uncalibrated_mastery_default: %d competency(ies) returned to uncalibrated, "
		  "%d left alone because a source records who chose the number"
		  % (len(cleared), len(kept)))
	if kept:
		print("  kept: %s" % ", ".join(sorted(kept)))
