# Copyright (c) 2026, SSSIHMS and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document

from sparsh_los.permissions import reject_identifiers


class SparshHumanReview(Document):

	def validate(self):
		# The guard covered what learners typed and nothing a reviewer wrote. A
		# reviewer explaining a verdict is the most likely person to paste a real
		# caregiver's detail into a stored field, and a review is never deleted.
		reject_identifiers(self.reviewer_comments)

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
		from sparsh_los import events

		# Emitted for every submitted review, not only the clearing ones: the count
		# that matters to the programme is how much review work was actually done.
		events.emit(
			events.HUMAN_REVIEW_COMPLETED,
			learner=self.learner,
			competency=self.competency,
			detail=f"status={self.review_status} clears_critical={int(bool(self.clears_critical_error))}",
			reference_doctype=self.doctype,
			reference_name=self.name,
		)

		# A review's verdict has to land on the evidence it examined, or "Rejected" and
		# "Needs More Evidence" are decisions the engine never sees. Four modules --
		# mastery, certification, orchestrator and the supervisor view -- exclude
		# rejected evidence from what a learner is credited with, and until now nothing
		# in the engine ever wrote that value: the only writer set "Approved". A
		# reviewer could reject a piece of evidence and watch it keep counting.
		if self.evidence and self.review_status in ("Approved", "Rejected"):
			evidence_doc = frappe.get_doc("Sparsh Evidence", self.evidence)
			if evidence_doc.learner != self.learner:
				frappe.throw(
					frappe._("That evidence belongs to a different learner"),
					frappe.ValidationError,
				)
			if evidence_doc.human_review_status != self.review_status:
				evidence_doc.db_set("human_review_status", self.review_status)

				from sparsh_los.mastery import recompute_mastery

				recompute_mastery(evidence_doc.learner, evidence_doc.competency)

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
