# Copyright (c) 2026, SSSIHMS and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document

from sparsh_los.permissions import reject_identifiers


class SparshAssessmentEvent(Document):
	def validate(self):
		reject_identifiers(self.detail, self.outcome_summary)

		if self.is_new():
			self.started_at = self.started_at or frappe.utils.now_datetime()

		before = self.get_doc_before_save()
		if before and before.status == "Completed" and self.status != "Completed":
			# A checkpoint that has been closed is a record of what was found. Reopening
			# it would let the same diagnostic be closed twice and assign its refreshers
			# twice, and would make the completion time a lie.
			frappe.throw(
				_("A completed assessment event cannot be reopened; start a new one"),
				frappe.PermissionError,
			)

		if self.status == "Completed" and not self.completed_at:
			self.completed_at = frappe.utils.now_datetime()

	def on_trash(self):
		"""The record of a checkpoint is evidence, like an attempt or an event log."""
		if frappe.flags.in_sparsh_maintenance or frappe.flags.in_uninstall:
			return

		frappe.throw(
			_("An assessment event records what a checkpoint found and cannot be deleted"),
			frappe.PermissionError,
		)


def on_doctype_update():
	# The two questions it is asked: what has this learner been through, and what is
	# still open.
	frappe.db.add_index("Sparsh Assessment Event", ["learner", "purpose"])
	frappe.db.add_index("Sparsh Assessment Event", ["status", "purpose"])
