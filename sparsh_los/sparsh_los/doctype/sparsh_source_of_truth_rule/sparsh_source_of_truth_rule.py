# Copyright (c) 2026, SSSIHMS and contributors
# For license information, please see license.txt

import re

import frappe
from frappe import _
from frappe.model.document import Document

RULE_ID_PATTERN = re.compile(r"^[A-Z0-9-]+$")


# Frappe derives the controller class as doctype.replace(" ", ""), which keeps the
# lowercase "of". The odd casing is required: renaming it breaks controller loading.
class SparshSourceofTruthRule(Document):
	def validate(self):
		if not RULE_ID_PATTERN.match(self.rule_id or ""):
			frappe.throw(_("Rule ID must contain only A-Z, 0-9 and hyphen"))

		if (self.version or 0) < 1:
			frappe.throw(_("Version must be 1 or greater"))

		if self.supersedes:
			if self.supersedes == self.name:
				frappe.throw(_("A rule cannot supersede itself"))

			previous = frappe.db.get_value(
				self.doctype, self.supersedes, ["rule_id", "version"], as_dict=True
			)
			if previous.rule_id != self.rule_id:
				frappe.throw(_("A rule can only supersede another version of the same Rule ID"))
			if previous.version >= (self.version or 0):
				frappe.throw(_("Superseded rule must have a lower version"))

		if not self.is_new():
			before = frappe.db.get_value(self.doctype, self.name, "status")
			if before == "Superseded" and self.status != "Superseded":
				frappe.throw(_("A superseded rule cannot be reverted to an active status"))

	def on_update(self):
		if self.supersedes:
			if frappe.db.get_value(self.doctype, self.supersedes, "status") != "Superseded":
				frappe.db.set_value(self.doctype, self.supersedes, "status", "Superseded")
