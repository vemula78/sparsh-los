# Copyright (c) 2026, SSSIHMS and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document

from sparsh_los.mastery import recompute_mastery


class SparshEvidence(Document):
	def validate(self):
		if self.assistance_level is None or self.assistance_level < 0 or self.assistance_level > 4:
			frappe.throw(_("Assistance level must be between 0 and 4"))

		if self.critical_error and self.outcome == "Pass":
			frappe.throw(_("Evidence carrying a critical error cannot record a passing outcome"))

		if not self.recorded_at:
			self.recorded_at = frappe.utils.now_datetime()

	def on_submit(self):
		recompute_mastery(self.learner, self.competency)

	def on_cancel(self):
		recompute_mastery(self.learner, self.competency)
