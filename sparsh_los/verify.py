# Copyright (c) 2026, SSSIHMS and contributors
# For license information, please see license.txt

"""Behavioural acceptance check for the engine.

Run with:  bench --site <site> execute sparsh_los.verify.run

Creates its own fixtures under the ZZV- prefix, exercises the eight named checks,
deletes everything it created, and exits non-zero on any failure. Re-runnable:
leftovers from an interrupted run are cleared before the first check.
No patient data, no real learner data, no network access.
"""

import re
import sys

import frappe

from sparsh_los.mastery import STATE_ORDER, derive_state

PREFIX = "ZZV-"
LEARNER = "Administrator"
DOMAIN = PREFIX + "DOM"
COMPETENCY = PREFIX + "COMP"
COMPETENCY_2 = PREFIX + "COMP2"
ACTIVITY_1 = PREFIX + "ACT1"
ACTIVITY_2 = PREFIX + "ACT2"
RULE_ID = PREFIX + "RULE"

PII_PATTERN = re.compile(r"patient|mrn|uhid|dob|aadhaar|phone|address", re.IGNORECASE)
DOMAIN_STRING_PATTERN = re.compile(r"sparsh|sai", re.IGNORECASE)

results = []


def _check(name, fn):
	try:
		fn()
		results.append((name, True, ""))
		print(f"PASS {name}")
	except Exception as exc:  # noqa: BLE001 - the harness reports, it does not handle
		frappe.db.rollback()
		results.append((name, False, str(exc)))
		print(f"FAIL {name}: {exc}")


def _assert(condition, message):
	if not condition:
		raise AssertionError(message)


def _raises(fn, message):
	frappe.db.savepoint("sparsh_verify")
	try:
		fn()
	except frappe.ValidationError:
		frappe.db.rollback(save_point="sparsh_verify")
		return
	except Exception as exc:  # noqa: BLE001
		frappe.db.rollback(save_point="sparsh_verify")
		raise AssertionError(f"{message} (raised {type(exc).__name__}: {exc})") from None
	frappe.db.rollback(save_point="sparsh_verify")
	raise AssertionError(message)


# --------------------------------------------------------------------- fixtures
def _delete_all(doctype, filters):
	for name in frappe.get_all(doctype, filters=filters, pluck="name"):
		doc = frappe.get_doc(doctype, name)
		if doc.meta.is_submittable and doc.docstatus == 1:
			doc.cancel()
		frappe.delete_doc(doctype, name, force=True, ignore_permissions=True)


def teardown():
	"""Delete fixtures in dependency order. Safe to call when nothing exists."""
	competencies = [COMPETENCY, COMPETENCY_2]
	_delete_all("Sparsh Certification Record", {"competency": ("in", competencies)})
	# Evidence before Mastery State: cancelling Evidence triggers a recompute that
	# recreates the Mastery row, so deleting Mastery first leaves one behind.
	_delete_all("Sparsh Evidence", {"competency": ("in", competencies)})
	_delete_all("Sparsh Mastery State", {"competency": ("in", competencies)})
	_delete_all("Sparsh Attempt", {"activity": ("in", [ACTIVITY_1, ACTIVITY_2])})
	_delete_all("Sparsh Activity", {"name": ("in", [ACTIVITY_1, ACTIVITY_2])})
	_delete_all("Sparsh Competency", {"name": ("in", competencies)})
	_delete_all("Sparsh Competency Domain", {"name": DOMAIN})
	_delete_all("Sparsh Source of Truth Rule", {"rule_id": RULE_ID})
	frappe.db.commit()


def setup():
	domain = frappe.new_doc("Sparsh Competency Domain")
	domain.domain_id = DOMAIN
	domain.domain_name = "Verification Domain"
	domain.insert(ignore_permissions=True)

	for competency_id in (COMPETENCY, COMPETENCY_2):
		competency = frappe.new_doc("Sparsh Competency")
		competency.competency_id = competency_id
		competency.competency_name = "Verification Competency"
		competency.domain = DOMAIN
		competency.insert(ignore_permissions=True)

	for activity_id in (ACTIVITY_1, ACTIVITY_2):
		activity = frappe.new_doc("Sparsh Activity")
		activity.activity_id = activity_id
		activity.title = "Verification Activity"
		activity.competency = COMPETENCY
		activity.activity_type = "Knowledge check"
		activity.instruction = "Verification instruction."
		activity.version = 1
		activity.append("hint_ladder", {"level": 0, "hint_text": "No hint."})
		activity.append("hint_ladder", {"level": 1, "hint_text": "First hint."})
		activity.insert(ignore_permissions=True)

	frappe.db.commit()


def _new_rule(version, supersedes=None):
	rule = frappe.new_doc("Sparsh Source of Truth Rule")
	rule.rule_id = RULE_ID
	rule.version = version
	rule.rule_statement = "Verification rule statement."
	rule.status = "Validated"
	rule.supersedes = supersedes
	rule.insert(ignore_permissions=True)
	return rule


def _new_attempt(rule_name, hint_level=0, outcome="Pass", critical_error=0,
				 activity=ACTIVITY_1):
	attempt = frappe.new_doc("Sparsh Attempt")
	attempt.learner = LEARNER
	attempt.activity = activity
	attempt.rule = rule_name
	attempt.hint_level_used = hint_level
	attempt.outcome = outcome
	attempt.critical_error = critical_error
	attempt.insert(ignore_permissions=True)
	return attempt


def _new_evidence(activity, outcome, assistance_level=0, critical_error=0,
				  competency=COMPETENCY, submit=True):
	evidence = frappe.new_doc("Sparsh Evidence")
	evidence.learner = LEARNER
	evidence.competency = competency
	evidence.activity = activity
	evidence.activity_version = 1
	evidence.outcome = outcome
	evidence.assistance_level = assistance_level
	evidence.critical_error = critical_error
	evidence.insert(ignore_permissions=True)
	if submit:
		evidence.submit()
	return evidence


def _state():
	return frappe.db.get_value(
		"Sparsh Mastery State", {"learner": LEARNER, "competency": COMPETENCY}, "state"
	)


# ----------------------------------------------------------------------- checks
state_box = {}


def check_rule_version_snapshot():
	rule_v1 = _new_rule(1)
	attempt_1 = _new_attempt(rule_v1.name)
	_assert(attempt_1.rule_version == 1, "A1 did not snapshot version 1")

	rule_v2 = _new_rule(2, supersedes=rule_v1.name)
	_assert(
		frappe.db.get_value("Sparsh Source of Truth Rule", rule_v1.name, "status")
		== "Superseded",
		"v1 was not marked Superseded",
	)

	attempt_1.reload()
	_assert(attempt_1.rule_version == 1, "A1 rule_version changed after v2 was created")

	attempt_2 = _new_attempt(rule_v2.name)
	_assert(attempt_2.rule_version == 2, "A2 did not snapshot version 2")

	def mutate():
		attempt_1.rule_version = 2
		attempt_1.save(ignore_permissions=True)

	_raises(mutate, "Editing a stored rule_version was allowed")
	frappe.db.commit()


def check_evidence_cannot_contradict_attempt():
	"""Evidence may not launder a critical attempt into a clean pass."""
	# No rule needed: this check is about the attempt/evidence relationship only.
	attempt = _new_attempt(None, outcome="Fail", critical_error=1)

	def launder():
		evidence = frappe.new_doc("Sparsh Evidence")
		evidence.learner = LEARNER
		evidence.competency = COMPETENCY
		evidence.activity = ACTIVITY_1
		evidence.activity_version = 1
		evidence.attempt = attempt.name
		evidence.outcome = "Pass"
		evidence.assistance_level = 0
		evidence.critical_error = 0
		evidence.insert(ignore_permissions=True)

	_raises(launder, "Evidence citing a critical attempt was allowed to record a pass")
	frappe.db.commit()


def check_mastery_not_directly_settable():
	def force_state():
		doc = frappe.new_doc("Sparsh Mastery State")
		doc.learner = LEARNER
		doc.competency = COMPETENCY_2
		doc.state = "Mastered"
		doc.insert(ignore_permissions=True)

	_assert(not frappe.flags.in_mastery_recompute, "recompute flag leaked from an earlier step")
	_raises(force_state, "A Mastery State was insertable directly")


def check_mastery_derived_from_evidence():
	_new_evidence(ACTIVITY_1, "Pass", assistance_level=0)
	states = frappe.get_all(
		"Sparsh Mastery State",
		filters={"learner": LEARNER, "competency": COMPETENCY},
		pluck="state",
	)
	_assert(len(states) == 1, f"expected exactly 1 Mastery State, found {len(states)}")
	_assert(
		states[0] in ("Demonstrated", "Mastered"),
		f"expected Demonstrated/Mastered, found {states[0]}",
	)
	state_box["after_pass"] = states[0]
	frappe.db.commit()


def check_critical_error_blocks():
	_new_evidence(ACTIVITY_2, "Fail", assistance_level=0, critical_error=1)
	state = _state()
	_assert(
		STATE_ORDER.index(state) <= STATE_ORDER.index("Practising"),
		f"critical error did not cap the state, found {state}",
	)
	_assert(
		derive_state(LEARNER, COMPETENCY) == state,
		"stored state and derived state disagree",
	)
	state_box["after_critical"] = state

	def certify():
		cert = frappe.new_doc("Sparsh Certification Record")
		cert.learner = LEARNER
		cert.competency = COMPETENCY
		cert.certification_status = "Full"
		cert.insert(ignore_permissions=True)

	_raises(certify, "Certification was allowed despite a standing critical error")
	frappe.db.commit()


def check_evidence_cancel_recomputes():
	before = _state()
	passing = frappe.get_all(
		"Sparsh Evidence",
		filters={
			"learner": LEARNER,
			"competency": COMPETENCY,
			"outcome": "Pass",
			"docstatus": 1,
		},
		pluck="name",
	)
	_assert(passing, "no submitted passing Evidence to cancel")
	frappe.get_doc("Sparsh Evidence", passing[0]).cancel()

	after = _state()
	_assert(
		STATE_ORDER.index(after) < STATE_ORDER.index(before),
		f"state did not regress on cancel ({before} -> {after})",
	)
	frappe.db.commit()


def _sparsh_doctypes():
	return frappe.get_all("DocType", filters={"module": "Sparsh LOS"}, pluck="name")


def check_no_pii_fields():
	offenders = []
	for doctype in _sparsh_doctypes():
		for field in frappe.get_meta(doctype).fields:
			if PII_PATTERN.search(field.fieldname or "") or PII_PATTERN.search(field.label or ""):
				offenders.append(f"{doctype}.{field.fieldname}")
	_assert(not offenders, f"identifiable-data fieldnames present: {offenders}")


def check_no_domain_strings():
	offenders = []
	for doctype in _sparsh_doctypes():
		for field in frappe.get_meta(doctype).fields:
			for attribute in ("label", "options", "description"):
				value = field.get(attribute)
				if not value or field.fieldtype in ("Link", "Table", "Table MultiSelect"):
					continue
				if DOMAIN_STRING_PATTERN.search(value):
					offenders.append(f"{doctype}.{field.fieldname}.{attribute}")
	_assert(not offenders, f"domain strings present: {offenders}")


def check_cleanup():
	teardown()
	for doctype, filters in (
		("Sparsh Attempt", {"activity": ("in", [ACTIVITY_1, ACTIVITY_2])}),
		("Sparsh Evidence", {"competency": ("in", [COMPETENCY, COMPETENCY_2])}),
		("Sparsh Mastery State", {"competency": ("in", [COMPETENCY, COMPETENCY_2])}),
		("Sparsh Source of Truth Rule", {"rule_id": RULE_ID}),
		("Sparsh Activity", {"name": ("in", [ACTIVITY_1, ACTIVITY_2])}),
		("Sparsh Competency", {"name": ("in", [COMPETENCY, COMPETENCY_2])}),
		("Sparsh Competency Domain", {"name": DOMAIN}),
	):
		count = frappe.db.count(doctype, filters)
		_assert(count == 0, f"{doctype} still holds {count} fixture rows")


CHECKS = (
	("rule_version_snapshot", check_rule_version_snapshot),
	("evidence_cannot_contradict_attempt", check_evidence_cannot_contradict_attempt),
	("mastery_not_directly_settable", check_mastery_not_directly_settable),
	("mastery_derived_from_evidence", check_mastery_derived_from_evidence),
	("critical_error_blocks", check_critical_error_blocks),
	("evidence_cancel_recomputes", check_evidence_cancel_recomputes),
	("no_pii_fields", check_no_pii_fields),
	("no_domain_strings", check_no_domain_strings),
	("cleanup", check_cleanup),
)


def run():
	results.clear()
	frappe.flags.in_mastery_recompute = False
	teardown()
	setup()

	try:
		for name, fn in CHECKS:
			_check(name, fn)
	finally:
		if results and results[-1][0] != "cleanup":
			teardown()

	passed = sum(1 for _, ok, _ in results if ok)
	failed = len(results) - passed
	print(f"RESULT passed={passed} failed={failed}")

	if failed:
		sys.exit(1)
