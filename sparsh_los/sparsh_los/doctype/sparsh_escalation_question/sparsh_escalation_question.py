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
		guard.

		The first version of this guard read `self.answered_by or frappe.session.user`
		and compared that to the learner -- which asked the document who answered it.
		`answered_by` is part of the document being saved, so a dual-role learner could
		answer their own question and attribute it to a colleague, and the guard saw a
		different name and allowed it. Who is writing is a fact about the request, never
		about the payload.
		"""
		if self.is_new():
			# `before_insert` has already blanked every reviewer-side field.
			return

		before = self.get_doc_before_save()
		was_answered = bool(before and before.status == "Answered")
		answering = self.status == "Answered" or self.answer_text or self.answered_by

		if was_answered:
			# An answer is what the learner was actually told. Editing it afterwards
			# rewrites the record of advice already given -- and the stale `answered_by`
			# made that edit look like the original reviewer's work.
			for field in ("answer_text", "disposition", "answered_by", "status"):
				if self.get(field) != before.get(field):
					frappe.throw(
						_("This question has already been answered and cannot be rewritten"),
						frappe.PermissionError,
					)
			return

		if not answering:
			return

		if frappe.session.user == self.learner:
			frappe.throw(_("You cannot answer your own question"), frappe.PermissionError)

		# Attribution is taken from the session, not accepted from the document, so a
		# stored answer always names the person who actually wrote it.
		self.answered_by = frappe.session.user
		self.answered_at = self.answered_at or frappe.utils.now_datetime()
		self.status = "Answered"

		if not (self.answer_text or "").strip():
			frappe.throw(_("An answer cannot be empty"))
		if not self.disposition:
			frappe.throw(_("An answer must be classified with a disposition"))
