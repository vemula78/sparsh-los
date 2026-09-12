# Copyright (c) 2026, SSSIHMS and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document

from sparsh_los.permissions import is_restricted, reject_identifiers


class SparshEscalationQuestion(Document):
	def before_insert(self):
		"""A learner raises a question; the reviewer owns everything after that.

		Without this a learner can file a question already marked Answered, write the
		answer themselves, and route it wherever they like.
		"""
		if not is_restricted():
			return

		self.learner = frappe.session.user
		self.status = "Open"
		self.routed_to = None
		self.answer_text = None
		self.raised_at = frappe.utils.now_datetime()

	def validate(self):
		reject_identifiers(self.question_text, self.answer_text)
