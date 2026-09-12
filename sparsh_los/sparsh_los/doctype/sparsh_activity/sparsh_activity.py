# Copyright (c) 2026, SSSIHMS and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document

MAX_HINTS = 4


class SparshActivity(Document):
	def validate(self):
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
