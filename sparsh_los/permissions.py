# Copyright (c) 2026, SSSIHMS and contributors
# For license information, please see license.txt

"""Row-level scoping for learners.

DocType permissions decide what a role may do to a kind of record. They cannot say
"only your own". A learner holding read on Evidence would otherwise read every other
learner's evidence, so these conditions narrow each learner-scoped DocType to rows
whose `learner` is the current user.

Reviewers and System Managers are unrestricted: reviewing requires seeing the cohort.
"""

import re

import frappe

# DocType -> the field naming the learner the row belongs to.
LEARNER_SCOPED = {
	"Sparsh Attempt": "learner",
	"Sparsh Evidence": "learner",
	"Sparsh Mastery State": "learner",
	"Sparsh Certification Record": "learner",
	"Sparsh Escalation Question": "learner",
}

UNRESTRICTED_ROLES = {"System Manager", "Sparsh Reviewer", "Administrator"}


def is_restricted(user=None):
	"""Public wrapper: True when the session belongs to a learner and nothing more."""
	return _is_restricted(user or frappe.session.user)


def _is_restricted(user):
	"""True when the user is a learner and nothing more."""
	if not user or user == "Administrator":
		return False

	roles = set(frappe.get_roles(user))
	if roles & UNRESTRICTED_ROLES:
		return False

	return "Sparsh Learner" in roles


def _conditions(doctype, user):
	if not _is_restricted(user):
		return ""

	field = LEARNER_SCOPED[doctype]
	return f"`tab{doctype}`.`{field}` = {frappe.db.escape(user)}"


def _permitted(doc, user):
	if not _is_restricted(user):
		return True

	return doc.get(LEARNER_SCOPED[doc.doctype]) == user


# Frappe maps one function per DocType, so each gets a named wrapper.
def attempt_query(user):
	return _conditions("Sparsh Attempt", user)


def evidence_query(user):
	return _conditions("Sparsh Evidence", user)


def mastery_state_query(user):
	return _conditions("Sparsh Mastery State", user)


def certification_record_query(user):
	return _conditions("Sparsh Certification Record", user)


def escalation_question_query(user):
	return _conditions("Sparsh Escalation Question", user)


def has_permission(doc, ptype=None, user=None):
	"""A learner may only touch rows that are their own."""
	return _permitted(doc, user or frappe.session.user)


# Identifiers that must never reach a training record. Deliberately narrow: the
# hospital's own MRN format and Aadhaar-length digit runs. A broad heuristic would
# reject legitimate clinical prose and teach people to work around the check.
IDENTIFIER_PATTERNS = (
	# The hospital's own MRN formats, with or without a separator.
	re.compile(r"\b(?:WS|PN|PS)[\s\-/]?\d{4,}\b", re.IGNORECASE),
	# Aadhaar-length digit runs, however they are grouped.
	re.compile(r"\b\d{4}[\s\-]?\d{4}[\s\-]?\d{4}\b"),
	# Indian mobile numbers, with or without the country code.
	re.compile(r"(?:\+91[\s\-]?)?\b[6-9]\d{9}\b"),
	re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+"),
)


def reject_identifiers(*values):
	"""Refuse text carrying a patient identifier.

	Scenarios are de-identified by policy; this stops the obvious accidents rather
	than pretending to detect every identifier.
	"""
	for value in values:
		if not value:
			continue

		for pattern in IDENTIFIER_PATTERNS:
			if pattern.search(str(value)):
				frappe.throw(
					frappe._(
						"This text looks like it contains a patient identifier. "
						"Training records must not carry identifiable data."
					),
					frappe.ValidationError,
				)


def throttle(doctype, field="learner", limit=60, minutes=10):
	"""Refuse an implausible burst of records from one user.

	Not a security control — the permission checks are. This stops one stuck client
	or one bored account filling the evidence tables with noise, which would make the
	programme's own reporting useless long before it caused any other harm.
	"""
	since = frappe.utils.add_to_date(frappe.utils.now_datetime(), minutes=-minutes)
	recent = frappe.db.count(
		doctype, {field: frappe.session.user, "creation": (">", since)}
	)
	if recent >= limit:
		frappe.throw(
			frappe._("Too many submissions in a short time. Please wait a moment."),
			frappe.ValidationError,
		)
