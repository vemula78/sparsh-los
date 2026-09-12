# Copyright (c) 2026, SSSIHMS and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document

from sparsh_los.mastery import derive_state, has_blocking_critical_error

CERTIFIABLE_STATES = ("Demonstrated", "Mastered")


class SparshCertificationRecord(Document):
	def validate(self):
		if has_blocking_critical_error(self.learner, self.competency):
			frappe.throw(
				_("An unresolved critical error blocks certification for this competency"),
				frappe.ValidationError,
			)

		state = derive_state(self.learner, self.competency)
		if state not in CERTIFIABLE_STATES:
			frappe.throw(
				_("Competency is at state {0}; certification requires Demonstrated or Mastered").format(state),
				frappe.ValidationError,
			)

		self.derived_state = state
