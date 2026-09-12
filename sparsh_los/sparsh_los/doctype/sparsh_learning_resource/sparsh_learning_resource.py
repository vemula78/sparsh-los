# Copyright (c) 2026, SSSIHMS and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document


class SparshLearningResource(Document):
	def validate(self):
		if self.supersedes == self.name:
			frappe.throw(_("A resource cannot supersede itself"))

	def on_update(self):
		"""Becoming Current retires the predecessor and makes its readers stale.

		The programme owner's requirement is explicit: when an approved resource
		changes materially, earlier evidence stays as it is and the competency is
		marked Refresh Due instead. Nothing here rewrites a learner's history.
		"""
		before = self.get_doc_before_save()
		became_current = self.status == "Current" and (not before or before.status != "Current")
		if not became_current or not self.supersedes:
			return

		frappe.db.set_value("Sparsh Learning Resource", self.supersedes, "status", "Superseded")

		from sparsh_los.refresher import on_resource_superseded

		on_resource_superseded(self.supersedes)
