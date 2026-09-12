# Copyright (c) 2026, SSSIHMS and contributors
# For license information, please see license.txt

"""Row-level scoping for learners.

DocType permissions decide what a role may do to a kind of record. They cannot say
"only your own". A learner holding read on Evidence would otherwise read every other
learner's evidence, so these conditions narrow each learner-scoped DocType to rows
whose `learner` is the current user.

Reviewers and System Managers are unrestricted: reviewing requires seeing the cohort.
"""

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
