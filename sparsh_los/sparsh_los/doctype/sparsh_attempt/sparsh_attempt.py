# Copyright (c) 2026, SSSIHMS and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document

from sparsh_los.permissions import is_restricted, reject_identifiers


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

		# The question is "is this person writing about themselves?", which
		# `is_restricted` cannot answer: it means "is this a reviewer?", so a user
		# holding both roles read as trusted and could file their own attempt claiming
		# outcome=Pass at hint level 0. A second reviewer turning that into Evidence
		# made it an unaided pass. Roles do not matter here; the subject does.
		if not self.learner:
			# Neither branch below matches a blank subject, so the submitted outcome
			# survived. `reqd` on the field caught it, but by accident of a JSON flag
			# rather than by this function, which is where the rule lives.
			frappe.throw(_("An attempt must name the learner it is about"))

		if self.learner == frappe.session.user:
			self.outcome = "Not Evaluated"
			self.critical_error = 0

			# Assistance is counted from the record, never taken from the writer —
			# the same rule the runner follows. Trusting the submitted level let a
			# self-filed attempt claim hint level 0 on an activity the person had
			# already been given hints for.
			from sparsh_los.runner import _session_position

			hint_level, retry_index = _session_position(self.learner, self.activity)
			self.hint_level_used = hint_level
			# Unconditional. Guarding on `is None` made this dead code -- the field
			# carries a default of 0, so a self-filer could set any retry index they
			# liked and the comment above would still claim both values came from the
			# record.
			self.retry_index = retry_index
			# Same reasoning for the timestamp: a self-filed attempt could carry any
			# time, on the one record this function exists to sanitise.
			self.attempted_at = frappe.utils.now_datetime()
			return

		# A reviewer filing an attempt about somebody else keeps the values they
		# supplied, and `review.record_evidence` only refuses evidence about *itself* —
		# so two reviewers, or one reviewer twice, can manufacture an unaided pass for a
		# learner. That is the same trust a reviewer already holds when they reject
		# evidence or assign a refresher, and it is recorded rather than denied: the
		# engine's protection against a corrupt reviewer is the audit trail, not a
		# check.
		if is_restricted():
			# A learner may not file an attempt under somebody else's name.
			self.learner = frappe.session.user
			self.outcome = "Not Evaluated"
			self.critical_error = 0

	def validate(self):
		reject_identifiers(self.response)

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

	def on_trash(self):
		"""Deleting a failed attempt would make a later answer look unaided.

		The role permission is gone too, but a privileged deletion path does not
		consult permissions, and this record is the assistance history. Data
		maintenance — uninstalling the app, clearing a test fixture — declares itself
		with a flag rather than working around the guard.
		"""
		if frappe.flags.in_sparsh_maintenance or frappe.flags.in_uninstall:
			return

		frappe.throw(
			_("An attempt is a historical record and cannot be deleted"), frappe.ValidationError
		)
