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

		from sparsh_los.mastery import recompute_mastery

		recompute_mastery(self.learner, self.competency)

	def after_insert(self):
		from sparsh_los.mastery import recompute_mastery

		recompute_mastery(self.learner, self.competency)
