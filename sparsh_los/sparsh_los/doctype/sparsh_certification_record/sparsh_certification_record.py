# Copyright (c) 2026, SSSIHMS and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document

from sparsh_los.mastery import derive_state, has_blocking_critical_error

CERTIFIABLE_STATES = ("Demonstrated", "Mastered")

# A certification that is still standing, whether or not its support currently holds.
STANDING = ("Active", "Suspended")


class SparshCertificationRecord(Document):
	def validate(self):
		if self.certification_status == "Revoked":
			self._validate_revocation()
			# Revocation must stay available precisely when competence has regressed.
			# Requiring Demonstrated to revoke would lock the record open after a
			# critical error, which is the opposite of what the gate is for.
			self.derived_state = derive_state(self.learner, self.competency)
			return

		if has_blocking_critical_error(self.learner, self.competency):
			frappe.throw(
				_("An unresolved critical error blocks certification for this competency"),
				frappe.ValidationError,
			)

		state = derive_state(self.learner, self.competency)
		if state not in CERTIFIABLE_STATES:
			frappe.throw(
				_("Competency is at state {0}; certification requires Demonstrated or Mastered").format(state),
				frappe.ValidationError,
			)

		self.derived_state = state

	def _validate_revocation(self):
		"""A revocation withdraws a specific certification, not the idea of one."""
		if not self.revokes:
			frappe.throw(
				_("A revocation must name the certification it withdraws"), frappe.ValidationError
			)

		target = frappe.db.get_value(
			"Sparsh Certification Record",
			self.revokes,
			["learner", "competency", "docstatus", "certification_state"],
			as_dict=True,
		)
		if not target or target.docstatus != 1:
			frappe.throw(_("The certification being revoked is not submitted"), frappe.ValidationError)

		if target.learner != self.learner or target.competency != self.competency:
			frappe.throw(
				_("A revocation must match the learner and competency it withdraws"),
				frappe.ValidationError,
			)

		if target.certification_state not in STANDING:
			frappe.throw(_("That certification is no longer standing"), frappe.ValidationError)

	def before_submit(self):
		# Attribution belongs to the act of certifying, not to the last draft save.
		self.certified_by = frappe.session.user
		self.certified_on = frappe.utils.now_datetime()

		if self.certification_status == "Revoked":
			self.certification_state = "Revoked"
			self.standing_key = None
			return

		# One standing certification per learner and competency, or nothing can answer
		# "is this person certified right now?".
		existing = frappe.db.exists(
			"Sparsh Certification Record",
			{
				"learner": self.learner,
				"competency": self.competency,
				"docstatus": 1,
				"certification_state": ("in", STANDING),
				"name": ("!=", self.name),
			},
		)
		if existing:
			frappe.throw(
				_("A certification already stands for this competency. Revoke {0} first.").format(
					existing
				),
				frappe.ValidationError,
			)

		# The check above loses a race between two concurrent submissions; this key is
		# what actually guarantees it, because the database rejects the duplicate.
		self.standing_key = f"{self.learner}::{self.competency}"

	def on_submit(self):
		if self.certification_status == "Revoked" and self.revokes:
			frappe.db.set_value(
				"Sparsh Certification Record",
				self.revokes,
				{"certification_state": "Revoked", "standing_key": None},
			)


def on_doctype_update():
	# One standing certification per learner and competency, enforced where it cannot
	# be raced: standing_key is NULL once revoked, and NULLs do not collide.
	frappe.db.add_unique(
		"Sparsh Certification Record", ["standing_key"], constraint_name="unique_standing_certification"
	)


@frappe.whitelist()
def current(learner, competency):
	"""The certification that stands right now, or None. The authoritative answer."""
	from sparsh_los.permissions import is_restricted

	if learner != frappe.session.user and is_restricted():
		frappe.throw(_("You can only view your own certification"), frappe.PermissionError)

	rows = frappe.get_all(
		"Sparsh Certification Record",
		filters={
			"learner": learner,
			"competency": competency,
			"docstatus": 1,
			"certification_state": ("in", STANDING),
		},
		fields=["name", "certification_status", "certification_state", "certified_on", "certified_by"],
		order_by="creation desc",
	)
	return rows[0] if rows else None
