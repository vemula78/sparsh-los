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

		# Always overwritten here for the same reason: a crafted document could
		# otherwise submit itself as version 1 over an existing certificate.
		self.certificate_version = _next_version(self.learner, self.competency, self.name)

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


def _next_version(learner, competency, exclude_name):
	"""The ordinal of a certificate issued now for this learner and competency.

	Every issue record that already exists -- standing, suspended, revoked or
	cancelled -- is earlier than this one, so the new ordinal is at least one more
	than their count, and at least one more than the highest version any of them
	carries. Taking the larger of the two keeps the sequence correct on a site whose
	older records predate the column and hold 0: those stay unnumbered (the backfill
	reports them), and the new certificate is still placed after all of them rather
	than issued as a second "version 1".

	Two concurrent issues would compute the same number. They also both set
	`standing_key`, and its unique index refuses the second, so uniqueness of
	(learner, competency, version) is held by that index rather than by a separate
	one: a version is only ever assigned in the transaction that takes the key.
	"""
	row = frappe.db.sql(
		"""select count(*) as issued, ifnull(max(certificate_version), 0) as highest
		   from `tabSparsh Certification Record`
		   where learner = %(learner)s and competency = %(competency)s
		     and docstatus in (1, 2)
		     and ifnull(certification_status, '') != 'Revoked'
		     and name != %(name)s""",
		{"learner": learner, "competency": competency, "name": exclude_name},
		as_dict=True,
	)[0]
	return max(int(row.issued or 0), int(row.highest or 0)) + 1


def on_doctype_update():
	# One standing certification per learner and competency, enforced where it cannot
	# be raced: standing_key is NULL once revoked, and NULLs do not collide.
	frappe.db.add_unique(
		"Sparsh Certification Record", ["standing_key"], constraint_name="unique_standing_certification"
	)


@frappe.whitelist()
def current(learner, competency):
	"""The certification that stands right now, or None. The authoritative answer."""
	from sparsh_los.permissions import is_restricted, require_enrolment

	require_enrolment()
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
		# Reported, not persisted. Writing here meant a read endpoint any learner can
		# call opened an in_sparsh_certification window — the one flag rule the app
		# states plainly. The stored record is reconciled by evidence changes and by
		# the daily expiry job; this answer is derived, so it does not go stale.
		row["certification_state"] = "Suspended"
		row["state_is_derived"] = True

	return row
