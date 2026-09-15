# Copyright (c) 2026, SSSIHMS and contributors
# For license information, please see license.txt

import re

import frappe
from frappe import _
from frappe.model.document import Document

RULE_ID_PATTERN = re.compile(r"^[A-Z0-9-]+$")



def _differs(before, after):
	"""Compare a stored value with a submitted one without tripping on type or blanks.

	A Date column comes back as `datetime.date` and is usually submitted as a string;
	an Int as 0 and None mean the same absence here. Comparing raw would report a
	change on every save and freeze rules that nobody edited.
	"""
	if before is None and after is None:
		return False
	if before in ("", None) and after in ("", None):
		return False
	return str(before).strip() != str(after).strip()


# Frappe derives the controller class as doctype.replace(" ", ""), which keeps the
# lowercase "of". The odd casing is required: renaming it breaks controller loading.
class SparshSourceofTruthRule(Document):
	# What a Validated rule says, and on whose authority. Frozen once it is in force:
	# the engine judges people against this wording, and a page that shows the owner's
	# approved statement beside the proposal is worth nothing if either can be edited
	# afterwards. Superseding with a new version is the supported way to change a rule;
	# that route keeps both versions and is deliberately still open.
	FROZEN_ONCE_VALIDATED = (
		"rule_id",
		"version",
		"rule_statement",
		"approved_statement",
		"approved_by_name",
		"approval_source",
		"effective_date",
		"criticality",
		"automation_status",
	)

	def validate(self):
		self._validated_means_somebody_validated_it()
		self._validated_wording_is_frozen()

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

	def _validated_wording_is_frozen(self):
		"""A rule already in force cannot be reworded, re-dated or re-attributed.

		`status` itself is not frozen: retiring a rule withdraws it and superseding it
		replaces it, and neither changes what the rule said while it was in force.
		Everything that constitutes the claim -- the wording, who approved it, the
		document that says so, the date it took effect, and whether a machine may apply
		it -- is fixed here.

		The guard reads the stored row rather than `self._doc_before_save`, which is
		None on a `db_set` path and would make the freeze depend on how the caller
		happened to write.
		"""
		if self.is_new():
			return

		before = frappe.db.get_value(
			self.doctype, self.name, ["status", *self.FROZEN_ONCE_VALIDATED], as_dict=True
		)
		if not before or before.status != "Validated":
			return

		changed = [
			field
			for field in self.FROZEN_ONCE_VALIDATED
			if _differs(before.get(field), self.get(field))
		]
		if not changed:
			return

		frappe.throw(
			_(
				"This rule is Validated and in force: {0} cannot be changed. Supersede it "
				"with a new version instead, which keeps both what was approved and what "
				"replaced it."
			).format(", ".join(sorted(changed))),
			frappe.ValidationError,
		)


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

		# `rule_owner` used to be required here, and it is a Link to `User`. That was
		# wrong, and it blocked the first real decision the guard ever met: it made
		# "approved this clinically" depend on "holds an account in Frappe". The
		# programme owner approved seventeen rules in a signed matrix and may never log
		# in at all. Requiring a login would have forced either a fabricated account or
		# an indefinite wait, and neither is what the build guide asks for -- it asks
		# that the owner be *named*, with an effective date and a source.
		#
		# So the authority is now the governance record: who approved it, which document
		# says so, and when it took effect. `rule_owner` remains for the case where the
		# approver does hold an account, and is not required.
		missing = [
			label
			for label, value in (
				("the approved wording", (self.approved_statement or "").strip()),
				("the name of the person who approved it", (self.approved_by_name or "").strip()),
				("the document the approval comes from", (self.approval_source or "").strip()),
				("an effective date", self.effective_date),
			)
			if not value
		]
		if missing:
			frappe.throw(
				_("A rule cannot be Validated without {0}. Validated means a named person "
				  "approved this wording on a date, in a document somebody can go and "
				  "read; without that the engine would score against text nobody signed.").format(
					", ".join(missing)
				)
			)

		# Who *entered* it is a fact about the request, never about the payload -- the
		# same rule that closed the self-answered escalation. It does not replace the
		# approver; it records who transcribed the approval, which is the person to ask
		# if the transcription is ever questioned.
		self.approval_recorded_by = frappe.session.user

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
