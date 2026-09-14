# Copyright (c) 2026, SSSIHMS and contributors
# For license information, please see license.txt

"""Number the certificates that were issued before `certificate_version` existed.

The column arrived with `DEFAULT 0`, so every issue record that predates it reads as
version 0 -- which is not a version. A learner and competency with exactly one issue
record is decidable: whatever else happened, that certificate was the first, and it is
numbered 1.

A pair with two or more unnumbered issue records is not decided here. Whether the
second was a re-certification after a lapse (version 2) or an amendment of the first
(still version 1) is a fact about what the programme did, and neither `creation` nor
`amended_from` settles it -- an amended record can carry either meaning. The patch
reports those pairs by record name and leaves every one of them at 0, where the
compliance view shows them as "version not determined" rather than as version 1.

Revocation records (`certification_status = Revoked`) are not certificates and are
not numbered. Record names are hashes; learner ids are not printed.
"""

import frappe


def execute():
	if not frappe.db.has_column("Sparsh Certification Record", "certificate_version"):
		return

	rows = frappe.db.sql(
		"""select name, learner, competency, certificate_version
		   from `tabSparsh Certification Record`
		   where docstatus in (1, 2)
		     and ifnull(certification_status, '') != 'Revoked'""",
		as_dict=True,
	)
	if not rows:
		return

	by_pair = {}
	for row in rows:
		by_pair.setdefault((row.learner, row.competency), []).append(row)

	numbered = 0
	undecided = []
	for records in by_pair.values():
		unnumbered = [r for r in records if not r.certificate_version]
		if not unnumbered:
			continue
		if len(records) == 1:
			frappe.db.set_value(
				"Sparsh Certification Record",
				records[0].name,
				"certificate_version",
				1,
				update_modified=False,
			)
			numbered += 1
			continue
		# More than one issue record, at least one unnumbered: the ordering is a
		# programme fact, not a migration's guess.
		undecided.append(sorted(r.name for r in records))

	# No commit: patch_handler commits after the patch, and a commit here would land
	# before the Patch Log row and re-run the patch on an interrupt.
	print(
		"backfill_certificate_versions: %d issue record(s) examined, %d numbered as "
		"version 1, %d learner/competency pair(s) with several unnumbered "
		"certificates left at 0" % (len(rows), numbered, len(undecided))
	)
	for names in sorted(undecided):
		print("  undecided: %s" % ", ".join(names))
