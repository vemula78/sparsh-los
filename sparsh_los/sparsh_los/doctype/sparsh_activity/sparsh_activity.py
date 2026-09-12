# Copyright (c) 2026, SSSIHMS and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document


class SparshActivity(Document):
	def validate(self):
		seen = set()
		for row in self.hint_ladder:
			if row.level is None or row.level < 0 or row.level > 4:
				frappe.throw(_("Hint ladder level must be between 0 and 4 (row {0})").format(row.idx))
			if row.level in seen:
				frappe.throw(_("Duplicate hint ladder level {0}").format(row.level))
			seen.add(row.level)

		if (self.version or 0) < 1:
			frappe.throw(_("Version must be 1 or greater"))
