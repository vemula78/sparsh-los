# Copyright (c) 2026, SSSIHMS and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document

from sparsh_los.permissions import is_restricted, reject_identifiers


class SparshEscalationQuestion(Document):
	def before_insert(self):
		"""A learner raises a question; the reviewer owns everything after that.

		Without this a learner can file a question already marked Answered, write the
		answer themselves, and route it wherever they like.

		The reviewer-side fields are reset for everyone, not only for restricted users.
		`is_restricted` answers "is this a reviewer?", which is the wrong question here:
		a user holding both the learner and reviewer roles is unrestricted, and the
		early return let them insert a self-answered question in one write, never
		reaching the self-check in `escalation.answer`. No legitimate path inserts a
		question that is already answered.
		"""
		self.status = "Open"
		self.routed_to = None
		self.answer_text = None
		self.answered_by = None
		self.answered_at = None
		self.disposition = None
		self.raised_at = frappe.utils.now_datetime()

		if is_restricted():
			self.learner = frappe.session.user

	def validate(self):
		reject_identifiers(self.question_text, self.answer_text)
		self._nobody_answers_their_own_question()

	def _nobody_answers_their_own_question(self):
		"""The invariant belongs on the document, because every write path passes here.

		`before_insert` closed the insert route and `escalation.answer` closed the
		endpoint, and between them sat the ordinary save: a reviewer holds write on this
		DocType, so a user with both roles could set status, answer_text and answered_by
		on their own question through the generic update API and never reach either
		guard. Closing two of three doors was worth nothing.
		"""
		answering = self.status == "Answered" or self.answer_text or self.answered_by
		if not answering:
			return

		actor = self.answered_by or frappe.session.user
		if actor == self.learner:
			frappe.throw(_("You cannot answer your own question"), frappe.PermissionError)
