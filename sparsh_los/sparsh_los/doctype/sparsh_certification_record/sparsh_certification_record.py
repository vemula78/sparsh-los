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
		if self.learner == frappe.session.user:
			frappe.throw(_("You cannot certify your own competence"), frappe.PermissionError)

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

		# Read-only in the form is not read-only to a crafted document: a certificate
		# could be submitted already Suspended or Revoked while still occupying the
		# standing key.
		self.certification_state = "Active"

	def on_update_after_submit(self):
		"""Standing is decided by reconciliation and revocation, not by editing.

		Both fields have to be allow-on-submit for the engine to maintain them, which
		would otherwise let anyone with write access hand themselves a standing
		certificate by nulling somebody else's key.
		"""
		if frappe.flags.in_mastery_recompute or frappe.flags.in_sparsh_certification:
			return

		frappe.throw(
			_("Certification standing is maintained by the system, not edited directly"),
			frappe.ValidationError,
		)

	def on_cancel(self):
		# Otherwise the unique key stays occupied by a cancelled record and no future
		# certification for this pair can be submitted.
		previous = frappe.flags.in_sparsh_certification
		frappe.flags.in_sparsh_certification = True
		try:
			self.db_set("standing_key", None)
			self.db_set("certification_state", "Revoked")
		finally:
			frappe.flags.in_sparsh_certification = previous

	def on_submit(self):
		if self.certification_status == "Revoked" and self.revokes:
			previous = frappe.flags.in_sparsh_certification
			frappe.flags.in_sparsh_certification = True
			try:
				frappe.db.set_value(
					"Sparsh Certification Record",
					self.revokes,
					{"certification_state": "Revoked", "standing_key": None},
				)
			finally:
				frappe.flags.in_sparsh_certification = previous


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
	if not rows:
		return None

	# Expiry is noticed at read time, and the daily job may not have run. Answering
	# "Active" for a competence that has since lapsed would be the wrong answer at
	# exactly the moment somebody is asking.
	from sparsh_los.mastery import DEMONSTRATED, MASTERED, derive_state

	row = rows[0]
	if derive_state(learner, competency) not in (DEMONSTRATED, MASTERED):
		# Persist it rather than only reporting it, or certificate_detail and the
		# stored record keep saying Active until the daily job runs.
		previous = frappe.flags.in_sparsh_certification
		frappe.flags.in_sparsh_certification = True
		try:
			frappe.db.set_value(
				"Sparsh Certification Record", row["name"], "certification_state", "Suspended"
			)
		finally:
			frappe.flags.in_sparsh_certification = previous
		row["certification_state"] = "Suspended"

	return row
