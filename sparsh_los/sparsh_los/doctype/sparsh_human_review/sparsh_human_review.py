# Copyright (c) 2026, SSSIHMS and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document


class SparshHumanReview(Document):

	def validate(self):
		# Provenance is copied from the evidence, never taken on trust: a review could
		# otherwise name one learner while clearing another's record.
		if self.evidence:
			row = frappe.db.get_value(
				"Sparsh Evidence", self.evidence, ["learner", "competency"], as_dict=True
			)
			if row:
				self.learner = row.learner
				self.competency = row.competency

	def before_submit(self):
		if self.learner == frappe.session.user:
			frappe.throw(
				frappe._("You cannot review your own evidence"), frappe.PermissionError
			)

		# Attribution belongs to the act of reviewing. Stamping it on every save meant
		# whoever last edited a draft became the recorded reviewer.
		self.reviewer = frappe.session.user
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

	def on_cancel(self):
		"""Withdrawing the review withdraws the clearance it granted."""
		if not (self.clears_critical_error and self.evidence):
			return

		evidence = frappe.get_doc("Sparsh Evidence", self.evidence)
		if evidence.cleared_by_review != self.name:
			return

		# Another standing review may still clear this evidence; withdrawing one
		# reviewer's decision should not discard another's.
		successor = frappe.db.get_value(
			"Sparsh Human Review",
			{
				"evidence": self.evidence,
				"docstatus": 1,
				"review_status": "Approved",
				"clears_critical_error": 1,
				"name": ("!=", self.name),
			},
			"name",
		)
		if successor:
			evidence.db_set("cleared_by_review", successor)
			return

		evidence.db_set("critical_error_cleared", 0)
		evidence.db_set("cleared_by_review", None)

		from sparsh_los.mastery import recompute_mastery

		recompute_mastery(evidence.learner, evidence.competency)
