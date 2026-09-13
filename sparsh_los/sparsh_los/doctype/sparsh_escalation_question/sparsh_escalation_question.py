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
		# Learner-side, so it is kept, not reset. Blank means Routine: a caller that
		# predates the field must file exactly the question it filed before.
		self.urgency = self.urgency or "Routine"

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
		if not before:
			return

		# Whose question this is cannot change. The guard below compares the session
		# user with `self.learner`, and `learner` was itself part of the payload: a
		# dual-role user could point the question at a colleague, answer it as
		# themselves, and point it back in a second save. Two saves, and the comparison
		# was true in neither of them. Nothing legitimate reassigns a question anyway.
		if self.learner != before.learner:
			frappe.throw(
				_("A question belongs to the learner who raised it"), frappe.PermissionError
			)

		if before.status == "Answered":
			# An answer is what the learner was actually told, and it is read together
			# with the question that produced it. Freezing only the answer fields let
			# the question, the attempt it cites and the context snapshot be rewritten
			# underneath it, so the same answer came to address something else.
			for field, value in before.as_dict().items():
				if field in ("modified", "modified_by", "_user_tags", "_comments",
							 "_assign", "_liked_by", "idx", "docstatus"):
					continue
				if self.get(field) != value:
					frappe.throw(
						_("This question has already been answered and cannot be changed"),
						frappe.PermissionError,
					)
			return

		answering = self.status == "Answered" or self.answer_text or self.answered_by
		if not answering:
			return

		if frappe.session.user == self.learner:
			frappe.throw(_("You cannot answer your own question"), frappe.PermissionError)

		# Attribution and timing are taken from the request, not accepted from the
		# document. `self.answered_at or now()` let a reviewer date their own answer.
		self.answered_by = frappe.session.user
		self.answered_at = frappe.utils.now_datetime()
		self.status = "Answered"

		if not (self.answer_text or "").strip():
			frappe.throw(_("An answer cannot be empty"))
		if not self.disposition:
			frappe.throw(_("An answer must be classified with a disposition"))

	def on_trash(self):
		"""Freezing an answered question is worth nothing if it can be deleted instead.

		`validate` refuses every edit once the question is Answered, but deletion does
		not pass through `validate`, and the installed System Manager DocPerm carries
		`delete`. The answer is what a learner was actually told, together with the
		question and the context that produced it -- removing the row removes the whole
		record of the exchange, which is the same loss the freeze exists to prevent.

		Data maintenance declares itself with a flag, as it does on Attempt and Event,
		rather than working around the guard.
		"""
		if frappe.flags.in_sparsh_maintenance or frappe.flags.in_uninstall:
			return

		if self.status == "Answered":
			frappe.throw(
				_("An answered question is the record of what a learner was told and cannot be deleted"),
				frappe.PermissionError,
			)
