# Copyright (c) 2026, SSSIHMS and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document


class SparshHumanReview(Document):

	def validate(self):
		# Attribution is a fact about who acted, not a field the caller may choose.
		self.reviewer = frappe.session.user
		if not self.reviewed_at:
			self.reviewed_at = frappe.utils.now_datetime()
