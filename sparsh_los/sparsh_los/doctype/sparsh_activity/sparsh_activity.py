# Copyright (c) 2026, SSSIHMS and contributors
# For license information, please see license.txt

from decimal import Decimal, InvalidOperation

import frappe
from frappe import _
from frappe.model.document import Document

from sparsh_los.permissions import reject_identifiers

MAX_HINTS = 4


class SparshActivity(Document):
	def validate(self):
		self._competency_is_settled()
		self._reflection_does_not_swallow_waiting_work()

		# Every field an author writes prose into. Scenarios are de-identified by
		# policy, but the guard only ran on learner responses, so a case built from a
		# real caregiver's record went into the content pack unchecked.
		reject_identifiers(
			self.title,
			self.instruction,
			self.expected_evidence,
			self.expected_response,
			self.expected_value,
			self.rubric_criteria,
			self.hints,
			self.critical_markers,
		)

		# The numeric answer key is text (blank must differ from zero), so its shape is
		# checked here rather than by the column. One number and nothing else: a unit
		# belongs in the instruction, and "120/80" is two numbers, not one.
		if (self.expected_value or "").strip():
			try:
				Decimal(self.expected_value.strip())
			except InvalidOperation:
				frappe.throw(
					_("Expected Value must be a single number, in the unit the instruction names")
				)

		if (self.tolerance or 0) < 0:
			frappe.throw(_("Tolerance cannot be negative"))

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

	def _reflection_does_not_swallow_waiting_work(self):
		"""Re-authoring an activity into Reflection must not empty the reviewer's queue.

		A reflection is stored and never queued: both `review.pending` and the
		programme summary exclude it by the activity's *current* evaluation mode. That
		is the right test for a new activity and the wrong one for an existing activity
		with attempts already waiting -- switching the mode makes every unreviewed
		attempt on it vanish from the queue and from the count in the same instant,
		with no record that anybody was waiting. A learner who asked for a verdict is
		then waiting on a queue nobody will ever see them in.

		Refused rather than migrated: deciding what those attempts were -- reflections
		after all, or work still owed a verdict -- is a judgement about the learner's
		submissions, not one a save should make silently.
		"""
		from sparsh_los.runner import NOT_SCORED_MODES

		if self.is_new() or (self.evaluation_mode or "") not in NOT_SCORED_MODES:
			return

		before = self.get_doc_before_save()
		if not before or (before.evaluation_mode or "") in NOT_SCORED_MODES:
			return

		waiting = frappe.db.sql(
			"""
			select count(*) from `tabSparsh Attempt` a
			where a.activity = %(activity)s
			  and a.outcome = 'Not Evaluated'
			  and not exists (select 1 from `tabSparsh Evidence` e where e.attempt = a.name)
			""",
			{"activity": self.name},
		)[0][0]

		if waiting:
			frappe.throw(
				_(
					"{0} attempt(s) on this activity are waiting for a reviewer. Making it "
					"a Reflection would remove them from the queue without anyone seeing "
					"them; clear the queue first."
				).format(waiting)
			)

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
