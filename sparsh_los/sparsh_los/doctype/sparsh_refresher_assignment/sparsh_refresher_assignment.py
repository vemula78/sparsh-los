# Copyright (c) 2026, SSSIHMS and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document


class SparshRefresherAssignment(Document):
	def before_insert(self):
		self.assigned_on = frappe.utils.now_datetime()

	def validate(self):
		self._nobody_closes_their_own_refresher()

		if self.status == "Completed" and not self.completed_on:
			self.completed_on = frappe.utils.now_datetime()

	def _nobody_closes_their_own_refresher(self):
		"""Closing your own refresher releases your own suspended certificate.

		This was the one input to a derived state with no self-guard on it. A refresher
		holds the competency at Refresh Due and suspends the certificate that depended
		on it; `on_update` recomputes on the open/closed edge. A user holding both the
		learner and the reviewer role -- reviewer carries `write` here -- could open
		their own assignment in Desk, set it to Completed, and have the engine restore
		their certification on their own say-so.

		The engine's own closing path is `refresher.close_satisfied`, which writes with
		`db.set_value` and never reaches `validate`, so it is unaffected: a refresher
		still closes by itself when the learner produces an independent pass.
		"""
		if frappe.flags.in_mastery_recompute:
			# The engine closed it from fresh evidence, not the learner.
			return

		if not self.learner or self.learner != frappe.session.user:
			return

		before = self.get_doc_before_save()
		if before and before.status == self.status:
			# Nothing about the open/closed state is changing; an unrelated edit by the
			# learner is not what this guards.
			return

		frappe.throw(
			_("You cannot close a refresher assigned to you; it closes when your work "
			  "shows the competency is current again"),
			frappe.PermissionError,
		)


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
		# Deleting it releases the hold exactly as completing it would, so it carries
		# the same self-guard. Placed here rather than in a second `on_trash`, which
		# would have silently replaced this one.
		if (
			self.learner
			and self.learner == frappe.session.user
			and not (frappe.flags.in_sparsh_maintenance or frappe.flags.in_uninstall)
		):
			frappe.throw(
				_("You cannot delete a refresher assigned to you"), frappe.PermissionError
			)

		if self.status != "Assigned":
			return

		self.flags.recompute_after_delete = (self.learner, self.competency)

	def after_delete(self):
		pair = self.flags.get("recompute_after_delete")
		if not pair:
			return

		from sparsh_los.mastery import recompute_mastery

		recompute_mastery(*pair)
