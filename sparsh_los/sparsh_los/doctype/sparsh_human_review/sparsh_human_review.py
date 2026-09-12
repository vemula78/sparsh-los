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

	def on_submit(self):
		"""A submitted review may clear the safety error it examined."""
		if not (self.clears_critical_error and self.evidence):
			return

		if self.review_status != "Approved":
			frappe.throw(
				frappe._("Only an approved review can clear a critical error"),
				frappe.ValidationError,
			)

		evidence = frappe.get_doc("Sparsh Evidence", self.evidence)
		if not evidence.critical_error:
			frappe.throw(
				frappe._("That evidence does not carry a critical error"), frappe.ValidationError
			)

		evidence.db_set("critical_error_cleared", 1)
		evidence.db_set("cleared_by_review", self.name)

		from sparsh_los.mastery import recompute_mastery

		recompute_mastery(evidence.learner, evidence.competency)
