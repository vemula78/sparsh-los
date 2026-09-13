# Copyright (c) 2026, SSSIHMS and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document

from sparsh_los.permissions import reject_identifiers

MAX_HINTS = 4


class SparshActivity(Document):
	def validate(self):
		self._competency_is_settled()

		# Every field an author writes prose into. Scenarios are de-identified by
		# policy, but the guard only ran on learner responses, so a case built from a
		# real caregiver's record went into the content pack unchecked.
		reject_identifiers(
			self.title,
			self.instruction,
			self.expected_evidence,
			self.expected_response,
			self.hints,
			self.critical_markers,
		)

		# The ladder is one hint per line, weakest first: line 1 is hint level 1.
		# It was a child table until a child table proved to be separately queryable
		# by the person being assessed, which made the answer key readable.
		hints = [line for line in (self.hints or "").splitlines() if line.strip()]
		if len(hints) > MAX_HINTS:
			frappe.throw(
				_("A hint ladder has at most {0} rungs; this one has {1}").format(
					MAX_HINTS, len(hints)
				)
			)

		if (self.version or 0) < 1:
			frappe.throw(_("Version must be 1 or greater"))

		if self.evaluation_mode == "Deterministic" and not (self.expected_response or "").strip():
			# Not an error: an activity can be authored before its answer is agreed.
			# It simply cannot auto-pass anybody until it has one.
			pass

	def _competency_is_settled(self):
		"""An activity cannot move to another competency once evidence cites it.

		Reassigning it would silently recredit historical evidence to a competency the
		learner was never assessed on.
		"""
		if self.is_new():
			return

		before = frappe.db.get_value(self.doctype, self.name, "competency")
		if before == self.competency:
			return

		if frappe.db.count("Sparsh Evidence", {"activity": self.name, "docstatus": 1}):
			frappe.throw(
				_(
					"This activity already has evidence against {0} and cannot be moved "
					"to another competency."
				).format(before),
				frappe.ValidationError,
			)
