# Copyright (c) 2026, SSSIHMS and contributors
# For license information, please see license.txt

import re

import frappe
from frappe import _
from frappe.model.document import Document

RULE_ID_PATTERN = re.compile(r"^[A-Z0-9-]+$")


# Frappe derives the controller class as doctype.replace(" ", ""), which keeps the
# lowercase "of". The odd casing is required: renaming it breaks controller loading.
class SparshSourceofTruthRule(Document):
	def validate(self):
		self._validated_means_somebody_validated_it()

		if not RULE_ID_PATTERN.match(self.rule_id or ""):
			frappe.throw(_("Rule ID must contain only A-Z, 0-9 and hyphen"))

		if (self.version or 0) < 1:
			frappe.throw(_("Version must be 1 or greater"))

		if self.supersedes:
			if self.supersedes == self.name:
				frappe.throw(_("A rule cannot supersede itself"))

			previous = frappe.db.get_value(
				self.doctype, self.supersedes, ["rule_id", "version"], as_dict=True
			)
			# A Link field is validated after this runs, so a name that does not exist
			# arrived here as None and raised AttributeError instead of the intended
			# message.
			if not previous:
				frappe.throw(_("The rule this supersedes does not exist"))
			if previous.rule_id != self.rule_id:
				frappe.throw(_("A rule can only supersede another version of the same Rule ID"))
			if previous.version >= (self.version or 0):
				frappe.throw(_("Superseded rule must have a lower version"))

		if not self.is_new():
			before = frappe.db.get_value(self.doctype, self.name, "status")
			if before == "Superseded" and self.status != "Superseded":
				frappe.throw(_("A superseded rule cannot be reverted to an active status"))

	def _validated_means_somebody_validated_it(self):
		"""Validated is a claim about a person, so the person has to be on the record.

		`status = "Validated"` is the single switch that lets the engine score against a
		rule automatically. Nothing guarded the transition: any writer could set it, on a
		rule still carrying the *candidate* wording, with no owner and no effective date
		-- and the engine would then treat unapproved text as programme policy. Section 8
		of the build guide marks owner, effective date and source Required, and the
		matrix's own instructions say "Use Validated only after explicit programme-owner /
		clinical sign-off" and "Enter the approved current rule in exact wording when
		confirmed".

		`rule_statement` holds the *candidate* -- what was proposed. `approved_statement`
		holds what the programme owner actually approved. Keeping both means a reader can
		always see what changed between the proposal and the decision; overwriting the
		candidate would destroy that, and it is the only evidence of what the engine was
		nearly configured to do.
		"""
		if self.status != "Validated":
			return

		missing = [
			label
			for label, value in (
				("the approved wording", (self.approved_statement or "").strip()),
				("a rule owner", self.rule_owner),
				("an effective date", self.effective_date),
			)
			if not value
		]
		if missing:
			frappe.throw(
				_("A rule cannot be Validated without {0}. Validated means a named person "
				  "approved this wording on a date; without that the engine would score "
				  "against text nobody signed.").format(", ".join(missing))
			)

	def on_update(self):
		if not self.supersedes:
			return

		if frappe.db.get_value(self.doctype, self.supersedes, "status") == "Superseded":
			return

		frappe.db.set_value(self.doctype, self.supersedes, "status", "Superseded")

		# A rule change does not quietly invalidate past assessments; it schedules the
		# people who were judged against the old rule to meet the new one.
		from sparsh_los.refresher import on_rule_superseded

		on_rule_superseded(self.supersedes)
