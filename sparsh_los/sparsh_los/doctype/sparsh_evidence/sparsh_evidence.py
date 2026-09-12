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

		self._reconcile_with_attempt()
		self._validate_activity_competency()

		if not self.recorded_at:
			self.recorded_at = frappe.utils.now_datetime()

	def _validate_activity_competency(self):
		"""Credit only the competency the activity actually belongs to.

		derive_state counts distinct activities, so without this two passes on
		activities from unrelated competencies could establish mastery of a third.
		"""
		if not (self.activity and self.competency):
			return

		owner_competency = frappe.db.get_value("Sparsh Activity", self.activity, "competency")
		if owner_competency and owner_competency != self.competency:
			frappe.throw(
				_("Activity {0} belongs to competency {1}, not {2}").format(
					self.activity, owner_competency, self.competency
				)
			)

	def _reconcile_with_attempt(self):
		"""Evidence may not contradict the attempt it cites.

		Without this an attempt flagged critical could be cited by evidence declaring
		critical_error=0 and outcome=Pass, which walks straight past the safety gate.
		"""
		if not self.attempt:
			return

		attempt = frappe.db.get_value(
			"Sparsh Attempt",
			self.attempt,
			["learner", "activity", "critical_error"],
			as_dict=True,
		)
		if not attempt:
			return

		if attempt.learner != self.learner:
			frappe.throw(_("Evidence and the attempt it cites must belong to the same learner"))

		if self.activity and attempt.activity and attempt.activity != self.activity:
			frappe.throw(_("Evidence and the attempt it cites must refer to the same activity"))

		if attempt.critical_error and not self.critical_error:
			# Inherit rather than reject: the critical error is a fact about what happened.
			self.critical_error = 1
			if self.outcome == "Pass":
				frappe.throw(
					_("Evidence citing an attempt with a critical error cannot record a pass")
				)

	def on_submit(self):
		recompute_mastery(self.learner, self.competency)

	def on_cancel(self):
		recompute_mastery(self.learner, self.competency)
