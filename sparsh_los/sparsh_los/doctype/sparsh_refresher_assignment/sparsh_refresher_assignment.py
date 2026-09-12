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

	def on_update(self):
		"""An open refresher holds the competency at Refresh Due; closing it releases.

		Without this the state that the assignment caused would never come back, and a
		learner who completed the work stayed stale and uncertified for ever. Only the
		open/closed edge is interesting, so nothing runs on an unchanged status.
		"""
		before = self.get_doc_before_save()
		if before and before.status == self.status:
			return

		from sparsh_los import events
		from sparsh_los.mastery import recompute_mastery

		if self.status == "Completed":
			events.emit(
				events.REFRESHER_COMPLETED,
				learner=self.learner,
				competency=self.competency,
				reference_doctype=self.doctype,
				reference_name=self.name,
			)
		recompute_mastery(self.learner, self.competency)

	def after_insert(self):
		"""Only the event here. `on_update` runs on insert too and owns the recompute.

		Recomputing in both re-read every piece of evidence and rewrote the child table
		twice for one assignment, which on a cohort-wide supersession is the difference
		between one pass and three per learner.
		"""
		from sparsh_los import events

		events.emit(
			events.REFRESHER_ASSIGNED,
			learner=self.learner,
			competency=self.competency,
			detail=f"trigger={self.trigger_reason}",
			reference_doctype=self.doctype,
			reference_name=self.name,
		)

	def on_trash(self):
		"""Deleting an open assignment removes the reason for Refresh Due.

		Without this the stored state kept the learner at Refresh Due, and their
		certificate Suspended, until some unrelated evidence event happened to
		recompute. Every other input to a derived state here has a symmetric guard.
		"""
		if self.status != "Assigned":
			return

		self.flags.recompute_after_delete = (self.learner, self.competency)

	def after_delete(self):
		pair = self.flags.get("recompute_after_delete")
		if not pair:
			return

		from sparsh_los.mastery import recompute_mastery

		recompute_mastery(*pair)
