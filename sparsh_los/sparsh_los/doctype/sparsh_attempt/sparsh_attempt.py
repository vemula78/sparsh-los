# Copyright (c) 2026, SSSIHMS and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document

from sparsh_los.permissions import is_restricted


class SparshAttempt(Document):
	def _snapshot_rule_version(self):
		# Snapshot the rule version once, at creation. Never fetch_from: the historical
		# version an attempt was judged against must not be rewritten later.
		if self.rule:
			self.rule_version = frappe.db.get_value(
				"Sparsh Source of Truth Rule", self.rule, "version"
			)
		else:
			self.rule_version = 0

	def before_insert(self):
		self._apply_learner_limits()
		self._snapshot_rule_version()

	def _apply_learner_limits(self):
		"""A learner may record an attempt, not adjudicate it.

		Without this a learner can post outcome=Pass, critical_error=0 for themselves
		or file an attempt under another learner's name.
		"""
		if frappe.flags.in_sparsh_runner:
			# The runner computes the verdict itself from stored content; the learner
			# never supplies it. Limiting it here would stop the engine grading at all.
			return

		if not is_restricted():
			return

		self.learner = frappe.session.user
		self.outcome = "Not Evaluated"
		self.critical_error = 0

	def validate(self):
		if self.hint_level_used is None or self.hint_level_used < 0 or self.hint_level_used > 4:
			frappe.throw(_("Hint level used must be between 0 and 4"))

		self.independent = 1 if self.hint_level_used == 0 else 0

		if self.critical_error:
			self.outcome = "Fail"

		if not self.attempted_at:
			self.attempted_at = frappe.utils.now_datetime()

		if not self.is_new():
			stored = frappe.db.get_value(self.doctype, self.name, "rule_version")
			if stored is not None and int(self.rule_version or 0) != int(stored):
				frappe.throw(_("Rule version is a historical snapshot and cannot be changed"))

			# An attempt records what a learner did at a point in time. Editing it after
			# the fact would make the evidence trail unreliable, so nothing may change.
			frappe.throw(_("An attempt is a historical record and cannot be edited once created"))
