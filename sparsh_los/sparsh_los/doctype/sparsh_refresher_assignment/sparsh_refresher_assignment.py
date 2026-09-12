# Copyright (c) 2026, SSSIHMS and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document


class SparshRefresherAssignment(Document):
	def before_insert(self):
		self.assigned_on = frappe.utils.now_datetime()

	def validate(self):
		if self.status == "Completed" and not self.completed_on:
			self.completed_on = frappe.utils.now_datetime()
