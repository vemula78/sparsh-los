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
import traceback

import frappe

from sparsh_los.mastery import STATE_ORDER, derive_state

PREFIX = "ZZV-"
PATHWAY = PREFIX + "PATH"
OTHER_DOMAIN = PREFIX + "DOM2"
OTHER_COMPETENCY = PREFIX + "COMP3"
OTHER_ACTIVITY = PREFIX + "ACT3"
LEARNER = "zzv-subject@example.invalid"
DOMAIN = PREFIX + "DOM"
COMPETENCY = PREFIX + "COMP"
COMPETENCY_2 = PREFIX + "COMP2"
ACTIVITY_1 = PREFIX + "ACT1"
ACTIVITY_2 = PREFIX + "ACT2"
RULE_ID = PREFIX + "RULE"
TEST_LEARNER = "zzv-learner@example.invalid"
DUAL_LEARNER = "zzv-dual@example.invalid"
OTHER_LEARNER = "zzv-other@example.invalid"

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
		# Some frappe exceptions stringify to nothing, which makes a failure unreadable.
		detail = str(exc).strip() or type(exc).__name__
		results.append((name, False, detail))
		print(f"FAIL {name}: {detail}")
		print(traceback.format_exc().strip().splitlines()[-4:][0])
		print(traceback.format_exc().strip().splitlines()[-2])


def _assert(condition, message):
	if not condition:
		raise AssertionError(message)


def _raises(fn, message, expect=None):
	"""Assert fn is refused — and, when `expect` is given, refused for the stated reason.

	Without `expect` this only proves something in the validation stack objected.
	frappe.PermissionError subclasses ValidationError, so a check could delete the rule
	it tests and still pass on an unrelated refusal further down.
	"""
	frappe.db.savepoint("sparsh_verify")
	try:
		fn()
	except frappe.ValidationError as exc:
		frappe.db.rollback(save_point="sparsh_verify")
		if expect and expect.lower() not in str(exc).lower():
			raise AssertionError(f"{message} (refused, but for another reason: {exc})") from None
		return
	except Exception as exc:  # noqa: BLE001
		frappe.db.rollback(save_point="sparsh_verify")
		raise AssertionError(f"{message} (raised {type(exc).__name__}: {exc})") from None
	frappe.db.rollback(save_point="sparsh_verify")
	raise AssertionError(message)


# --------------------------------------------------------------------- fixtures
def _delete_mastery(filters):
	"""Mastery is undeletable by design; cleanup is the engine acting, so it says so."""
	previous_flag = frappe.flags.in_mastery_recompute
	frappe.flags.in_mastery_recompute = True
	try:
		_delete_all("Sparsh Mastery State", filters)
	finally:
		frappe.flags.in_mastery_recompute = previous_flag


def _delete_all(doctype, filters):
	# Fixture cleanup is maintenance, and says so rather than routing around the
	# guards that make attempts and mastery states undeletable in ordinary use.
	previous = frappe.flags.in_sparsh_maintenance
	frappe.flags.in_sparsh_maintenance = True
	try:
		_delete_each(doctype, filters)
	finally:
		frappe.flags.in_sparsh_maintenance = previous


def _delete_each(doctype, filters):
	for name in frappe.get_all(doctype, filters=filters, pluck="name"):
		doc = frappe.get_doc(doctype, name)
		if doc.meta.is_submittable and doc.docstatus == 1:
			doc.cancel()
		frappe.delete_doc(doctype, name, force=True, ignore_permissions=True)


def _reset_competency(competency=None):
	"""Clear evidence-derived state for one competency, back-links first."""
	competency = competency or COMPETENCY
	for name in frappe.get_all("Sparsh Evidence", filters={"competency": competency}, pluck="name"):
		frappe.db.set_value("Sparsh Evidence", name, "cleared_by_review", None)
		# Uncleared critical evidence refuses to cancel, which is correct in the app
		# and inconvenient only here.
		frappe.db.set_value("Sparsh Evidence", name, "critical_error", 0)
	frappe.db.commit()

	_delete_all("Sparsh Human Review", {"competency": competency})
	_delete_all("Sparsh Certification Record", {"competency": competency})
	_delete_all("Sparsh Evidence", {"competency": competency})
	_delete_mastery({"competency": competency})
	_delete_all("Sparsh Attempt", {"activity": ("in", [ACTIVITY_1, ACTIVITY_2])})
	# An open refresher now holds the competency at Refresh Due, so a stale one left by
	# an earlier check reads as a regression in the check that follows it.
	_delete_all("Sparsh Refresher Assignment", {"competency": competency})
	# Rules too. A Draft rule left linked by an earlier check now correctly stops the
	# runner auto-scoring, which would read as a regression in the next check rather
	# than as the leftover fixture it is.
	if frappe.db.exists("Sparsh Competency", competency):
		doc = frappe.get_doc("Sparsh Competency", competency)
		if doc.get("linked_rules"):
			doc.set("linked_rules", [])
			doc.save(ignore_permissions=True)
	_delete_all("Sparsh Source of Truth Rule", {"rule_id": PREFIX + "RULE"})
	frappe.db.commit()


def teardown():
	"""Delete fixtures in dependency order. Safe to call when nothing exists."""
	competencies = [COMPETENCY, COMPETENCY_2, OTHER_COMPETENCY]
	# Evidence and Human Review point at each other; break the back-link first or
	# neither can be deleted.
	for name in frappe.get_all(
		"Sparsh Evidence", filters={"competency": ("in", competencies)}, pluck="name"
	):
		frappe.db.set_value("Sparsh Evidence", name, "cleared_by_review", None)
		frappe.db.set_value("Sparsh Evidence", name, "critical_error", 0)
	frappe.db.commit()
	_delete_all("Sparsh Refresher Assignment", {"competency": ("in", competencies)})
	_delete_all("Sparsh Human Review", {"competency": ("in", competencies)})
	_delete_all("Sparsh Certification Record", {"competency": ("in", competencies)})
	# Evidence before Mastery State: cancelling Evidence triggers a recompute that
	# recreates the Mastery row, so deleting Mastery first leaves one behind.
	_delete_all("Sparsh Evidence", {"competency": ("in", competencies)})
	_delete_mastery({"competency": ("in", competencies)})
	_delete_all("Sparsh Escalation Question", {"learner": ("in", [LEARNER, TEST_LEARNER, OTHER_LEARNER])})
	_delete_all("Sparsh Attempt", {"activity": ("like", PREFIX + "%")})
	_delete_all("Sparsh Activity", {"name": ("like", PREFIX + "%")})
	_delete_all("Sparsh Competency", {"name": ("in", competencies)})
	_delete_all("Sparsh Pathway", {"name": PATHWAY})
	_delete_all("Sparsh Learning Resource", {"resource_id": ("like", PREFIX + "%")})
	# Events are undeletable outside maintenance by design, which is exactly why the
	# teardown has to clear them: otherwise every run leaves rows pointing at deleted
	# fixtures, and the module's claim to delete everything it created stops being true.
	_delete_all("Sparsh Event", {"learner": ("like", PREFIX.lower() + "%")})
	_delete_all("Sparsh Event", {"competency": ("in", competencies)})
	# Same reasoning as events: undeletable outside maintenance, and its rows point at
	# fixture learners this teardown is about to delete.
	_delete_all("Sparsh Model Interaction", {"provider": ("like", "zzv%")})
	_delete_all("Sparsh Competency Domain", {"name": ("in", [DOMAIN, OTHER_DOMAIN])})
	_delete_all("Sparsh Source of Truth Rule", {"rule_id": RULE_ID})
	# Everything belonging to the fixture learners, whatever competency it names.
	# Deleting the user first left evidence behind, and cancelling that evidence on a
	# later run tried to recompute mastery for a learner who no longer existed.
	fixture_users = (
		LEARNER,
		TEST_LEARNER,
		OTHER_LEARNER,
		DUAL_LEARNER,
		"zzv-fresh@example.invalid",
		"zzv-stranger@example.invalid",
		PREFIX + "pathway@example.invalid",
	)
	for name in frappe.get_all(
		"Sparsh Evidence", filters={"learner": ("in", fixture_users)}, pluck="name"
	):
		frappe.db.set_value("Sparsh Evidence", name, "cleared_by_review", None)
		frappe.db.set_value("Sparsh Evidence", name, "critical_error", 0)
	frappe.db.commit()

	_delete_all("Sparsh Human Review", {"learner": ("in", fixture_users)})
	_delete_all("Sparsh Certification Record", {"learner": ("in", fixture_users)})
	_delete_all("Sparsh Evidence", {"learner": ("in", fixture_users)})
	_delete_mastery({"learner": ("in", fixture_users)})
	_delete_all("Sparsh Refresher Assignment", {"learner": ("in", fixture_users)})
	_delete_all("Sparsh Escalation Question", {"learner": ("in", fixture_users)})
	_delete_all("Sparsh Attempt", {"learner": ("in", fixture_users)})

	for user in fixture_users:
		if frappe.db.exists("User", user):
			frappe.delete_doc("User", user, force=True, ignore_permissions=True)
	frappe.db.commit()


def setup():
	# The verification subject is a real learner account: a reviewer may not write
	# evidence about their own work, so the harness cannot be its own subject.
	_make_learner(LEARNER)
	frappe.db.commit()

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
		activity.hints = "First hint.\nSecond hint."
		activity.insert(ignore_permissions=True)

	frappe.db.commit()


def _new_rule(version, supersedes=None, status="Validated"):
	rule = frappe.new_doc("Sparsh Source of Truth Rule")
	rule.rule_id = RULE_ID
	rule.version = version
	rule.rule_statement = "Verification rule statement."
	rule.status = status
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
				  competency=COMPETENCY, submit=True, learner=None):
	evidence = frappe.new_doc("Sparsh Evidence")
	evidence.learner = learner or LEARNER
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


def _submit(activity, response, as_user=None):
	"""Run one practice submission as the learner, not as the reviewer running this."""
	from sparsh_los import runner

	original_user = frappe.session.user
	try:
		frappe.set_user(as_user or LEARNER)
		return runner.submit(activity, response)
	finally:
		frappe.set_user(original_user)


def _state(learner=None):
	return frappe.db.get_value(
		"Sparsh Mastery State",
		{"learner": learner or LEARNER, "competency": COMPETENCY},
		"state",
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


def _make_learner(email):
	if frappe.db.exists("User", email):
		return frappe.get_doc("User", email)

	user = frappe.new_doc("User")
	user.email = email
	user.first_name = "Verification"
	user.enabled = 1
	user.user_type = "System User"
	user.append("roles", {"role": "Sparsh Learner"})
	user.insert(ignore_permissions=True)
	return user


def check_permission_model():
	"""Roles exist and hold the intended DocType permissions."""
	from sparsh_los.install import ROLES

	for role in ROLES:
		_assert(frappe.db.exists("Role", role), f"Role {role} is missing")

	def perms(doctype, role):
		row = frappe.db.get_value(
			"DocPerm",
			{"parent": doctype, "role": role},
			["`read`", "`write`", "`create`", "`submit`"],
			as_dict=True,
		)
		return row

	learner_evidence = perms("Sparsh Evidence", "Sparsh Learner")
	_assert(learner_evidence and learner_evidence.read == 1, "Learner cannot read Evidence")
	_assert(learner_evidence.create == 0, "Learner can create Evidence")
	_assert(learner_evidence.submit == 0, "Learner can submit Evidence")

	reviewer_evidence = perms("Sparsh Evidence", "Sparsh Reviewer")
	_assert(reviewer_evidence and reviewer_evidence.submit == 1, "Reviewer cannot submit Evidence")

	learner_attempt = perms("Sparsh Attempt", "Sparsh Learner")
	_assert(learner_attempt and learner_attempt.create == 1, "Learner cannot create Attempts")

	# Mastery is derived: no role may create or edit it, System Manager included.
	for role in ("System Manager", "Sparsh Reviewer", "Sparsh Learner"):
		row = perms("Sparsh Mastery State", role)
		if row:
			_assert(row.create == 0 and row.write == 0, f"{role} can write Mastery State")

	_assert(
		perms("Sparsh Human Review", "Sparsh Learner") is None,
		"Learner holds permissions on Human Review",
	)


def check_learner_row_scope():
	"""A learner sees only their own rows."""
	from sparsh_los.permissions import attempt_query, has_permission

	_make_learner(TEST_LEARNER)
	_make_learner(OTHER_LEARNER)
	frappe.db.commit()

	condition = attempt_query(TEST_LEARNER)
	_assert(TEST_LEARNER in (condition or ""), "Learner query condition does not scope by learner")
	_assert(
		attempt_query("Administrator") == "",
		"Administrator was scoped like a learner",
	)

	attempt = _new_attempt(None, outcome="Pass")
	attempt.reload()
	_assert(
		not has_permission(attempt, "read", TEST_LEARNER),
		"A learner was granted access to another learner's attempt",
	)
	_assert(
		has_permission(attempt, "read", "Administrator"),
		"Administrator was denied access to an attempt",
	)
	frappe.db.commit()


def check_learner_cannot_escape_scope():
	"""Authenticate as a learner and try the real access paths."""
	_make_learner(TEST_LEARNER)
	_make_learner(OTHER_LEARNER)
	frappe.db.commit()

	# An attempt owned by somebody else.
	foreign = _new_attempt(None, outcome="Pass")
	frappe.db.commit()

	original_user = frappe.session.user
	try:
		frappe.set_user(TEST_LEARNER)

		visible = frappe.get_list(
			"Sparsh Attempt", filters={"name": foreign.name}, ignore_permissions=False
		)
		_assert(not visible, "get_list exposed another learner's attempt")

		from frappe.client import get_value as client_get_value

		try:
			leaked = client_get_value("Sparsh Attempt", "outcome", {"name": foreign.name})
		except frappe.PermissionError:
			leaked = None
		_assert(not leaked, "frappe.client.get_value exposed another learner's attempt")

		# Filing under another learner's name is refused outright: the has_permission
		# veto runs before before_insert, so it never reaches the field correction.
		def file_for_another():
			doc = frappe.new_doc("Sparsh Attempt")
			doc.learner = OTHER_LEARNER
			doc.activity = ACTIVITY_1
			doc.hint_level_used = 0
			doc.insert()

		try:
			file_for_another()
			raise AssertionError("A learner filed an attempt for another learner")
		except frappe.PermissionError:
			pass

		# Their own attempt is accepted, but they do not get to grade it.
		own = frappe.new_doc("Sparsh Attempt")
		own.learner = TEST_LEARNER
		own.activity = ACTIVITY_1
		own.hint_level_used = 0
		own.outcome = "Pass"
		own.critical_error = 1
		own.insert()
		_assert(own.outcome == "Not Evaluated", "A learner graded their own attempt")
		_assert(not own.critical_error, "A learner set their own critical_error flag")

		def make_evidence():
			doc = frappe.new_doc("Sparsh Evidence")
			doc.learner = TEST_LEARNER
			doc.competency = COMPETENCY
			doc.activity = ACTIVITY_1
			doc.activity_version = 1
			doc.outcome = "Pass"
			doc.assistance_level = 0
			doc.insert()

		try:
			make_evidence()
			raise AssertionError("A learner created Evidence")
		except frappe.PermissionError:
			pass
	finally:
		frappe.set_user(original_user)

	frappe.db.rollback()
	frappe.db.commit()


def check_mastery_requires_distinct_activities():
	"""Two passes on one activity is not mastery; two on distinct activities is."""
	# Earlier checks leave a standing critical error, which no longer self-clears.
	_reset_competency()
	_new_evidence(ACTIVITY_1, "Pass")
	_assert(_state() == "Demonstrated", f"One pass gave {_state()}, expected Demonstrated")

	_new_evidence(ACTIVITY_1, "Pass")
	_assert(
		_state() == "Demonstrated",
		f"Two passes on the same activity gave {_state()}, expected Demonstrated",
	)

	_new_evidence(ACTIVITY_2, "Pass")
	_assert(_state() == "Mastered", f"Two distinct activities gave {_state()}, expected Mastered")
	frappe.db.commit()


def check_evidence_activity_must_match_competency():
	"""An activity cannot credit a competency it does not belong to."""

	def cross_credit():
		_new_evidence(ACTIVITY_1, "Pass", competency=COMPETENCY_2, submit=False)

	_raises(cross_credit, "Evidence credited a competency the activity does not belong to")
	frappe.db.commit()


def check_runner_loop():
	"""The runner grades, gives the minimum help, and writes evidence on a pass."""
	from sparsh_los import runner

	# Start from a known baseline: earlier checks leave this competency at Mastered,
	# and a test that depends on the order of other tests proves nothing.
	_reset_competency()
	_assert(_state() is None, "Baseline was not clear before the runner check")

	for name in (ACTIVITY_1, ACTIVITY_2):
		activity = frappe.get_doc("Sparsh Activity", name)
		activity.evaluation_mode = "Deterministic"
		activity.expected_response = "level two"
		activity.critical_markers = "stop the medicine"
		activity.save(ignore_permissions=True)
	frappe.db.commit()

	opened = runner.start(ACTIVITY_1)
	_assert(opened["instruction"], "Runner returned no instruction")
	_assert(opened["hint_level"] == 0, "A session did not open at hint level 0")

	wrong = _submit(ACTIVITY_1, "level four")
	_assert(wrong["outcome"] == "Fail", "A wrong answer was not marked Fail")
	_assert(wrong["hint_level"] == 1, "The hint ladder did not advance")
	_assert(wrong["hint"], "No hint was returned after a wrong answer")
	_assert("evidence" not in wrong, "A failed attempt produced evidence")
	_assert(_state() != "Demonstrated", "A failed attempt moved the mastery state")

	# A pass after a hint is practice evidence, not demonstration. The distinction is
	# the point of the hint ladder: help received is part of the record.
	right = _submit(ACTIVITY_1, "  Level Two  ")
	_assert(right["outcome"] == "Pass", "A correct answer was not marked Pass")
	_assert(right.get("evidence"), "A pass produced no evidence")
	_assert(
		_state() == "Practising",
		f"An assisted pass gave {_state()}, expected Practising",
	)

	assistance = frappe.db.get_value("Sparsh Evidence", right["evidence"], "assistance_level")
	_assert(assistance == 1, f"Assistance level recorded as {assistance}, expected 1")

	# The regression this guards: take a hint, pass with it, then resubmit the now-known
	# answer and have the counter reset because the last attempt was a pass. Nothing
	# above catches that — no submission there follows a pass on the same activity.
	again = _submit(ACTIVITY_1, "level two")
	repeat_assistance = frappe.db.get_value(
		"Sparsh Evidence", again["evidence"], "assistance_level"
	)
	_assert(
		repeat_assistance >= 1,
		f"Assistance fell to {repeat_assistance} on a repeat pass after a hint was shown",
	)

	# An unaided pass is what moves the learner to Demonstrated. A fresh activity is
	# needed: assistance is now counted from the record, and this one has a failure.
	independent = _submit(ACTIVITY_2, "level two")
	_assert(independent["outcome"] == "Pass", "An unaided correct answer was not marked Pass")
	_assert(
		_state() == "Demonstrated",
		f"An independent pass gave {_state()}, expected Demonstrated",
	)
	frappe.db.commit()


def check_runner_flags_critical_response():
	"""A response matching a declared critical error fails and blocks progression."""
	from sparsh_los import runner

	result = _submit(ACTIVITY_1, "I would tell them to stop the medicine")
	_assert(result["critical_error"] == 1, "A critical response was not flagged")
	_assert(result["outcome"] == "Fail", "A critical response was not failed")
	_assert(result.get("evidence"), "A critical response recorded no evidence")

	state = _state()
	_assert(
		state not in ("Demonstrated", "Mastered"),
		f"A standing critical error left the state at {state}",
	)
	frappe.db.commit()


def check_escalation_to_human_review():
	"""A learner escalates; only a reviewer answers; the answer is classified."""
	from sparsh_los import escalation

	_make_learner(TEST_LEARNER)
	frappe.db.commit()

	original_user = frappe.session.user
	try:
		frappe.set_user(TEST_LEARNER)

		own_attempt = frappe.new_doc("Sparsh Attempt")
		own_attempt.learner = TEST_LEARNER
		own_attempt.activity = ACTIVITY_1
		own_attempt.hint_level_used = 0
		own_attempt.insert()

		question = escalation.raise_question(
			"Should I advise stopping this medicine?",
			activity=ACTIVITY_1,
			attempt=own_attempt.name,
			reason="Safety critical",
		)
		doc = frappe.get_doc("Sparsh Escalation Question", question)
		_assert(doc.learner == TEST_LEARNER, "The question was not attributed to the learner")
		_assert(doc.status == "Open", f"A raised question opened at {doc.status}")
		_assert(doc.context_snapshot, "No context was packaged with the question")
		_assert(ACTIVITY_1 in doc.context_snapshot, "The activity was not captured in the context")

		# Answering is a reviewer action.
		try:
			escalation.answer(question, "No. Refer to the clinician.", "Private answer")
			raise AssertionError("A learner answered their own escalation")
		except frappe.PermissionError:
			pass
	finally:
		frappe.set_user(original_user)

	result = escalation.answer(
		question, "Outside volunteer scope. Refer to the clinician.", "Source-of-truth update"
	)
	_assert(result["disposition"] == "Source-of-truth update", "The disposition was not recorded")
	_assert(result["programme_change_required"], "A programme-level disposition was not flagged")

	doc = frappe.get_doc("Sparsh Escalation Question", question)
	_assert(doc.status == "Answered", f"An answered question sits at {doc.status}")
	_assert(doc.answered_by == frappe.session.user, "The answer was not attributed")

	def bad_disposition():
		escalation.answer(question, "text", "Something else")

	_raises(bad_disposition, "An unrecognised disposition was accepted")

	queue = escalation.open_queue()
	_assert(
		all(q["name"] != question for q in queue),
		"An answered question is still in the open queue",
	)
	frappe.db.commit()


def check_dashboards():
	"""The supervisor view answers its four questions from evidence."""
	from sparsh_los import dashboard

	_reset_competency()
	_new_evidence(ACTIVITY_1, "Pass")
	_new_evidence(ACTIVITY_2, "Pass")
	frappe.db.commit()

	learner = dashboard.learner_view(LEARNER)
	_assert(learner["learner"] == LEARNER, "The learner view named the wrong learner")
	_assert(
		COMPETENCY in learner["demonstrated"],
		f"A demonstrated competency is missing from {learner['demonstrated']}",
	)

	view = dashboard.supervisor_view(competency=COMPETENCY)
	_assert(view["learners"] >= 1, "The supervisor view found no learners")
	_assert(
		any(r["learner"] == LEARNER for r in view["ready_to_progress"]),
		"A demonstrated learner is not shown as ready to progress",
	)
	_assert(view["state_distribution"], "No state distribution was produced")

	heatmap = dashboard.competency_heatmap()
	_assert(COMPETENCY in heatmap["competencies"], "The heatmap is missing the competency")
	_assert(
		heatmap["rows"].get(LEARNER, {}).get(COMPETENCY) in ("Demonstrated", "Mastered"),
		"The heatmap cell does not carry the mastery state",
	)
	frappe.db.commit()


def check_other_domain_runs_unchanged():
	"""Acceptance criterion 13, tested rather than asserted.

	A competency from an entirely different domain — a digital-skills task, no clinical
	content — must run the same loop end to end with no schema change. If this needs a
	new field or a new code path, the engine is not domain-agnostic and the claim that
	it can host TechLingo or an HCP pack later is not true.
	"""
	from sparsh_los import dashboard, runner

	domain = frappe.new_doc("Sparsh Competency Domain")
	domain.domain_id = OTHER_DOMAIN
	domain.domain_name = "Digital skills"
	domain.insert(ignore_permissions=True)

	competency = frappe.new_doc("Sparsh Competency")
	competency.competency_id = OTHER_COMPETENCY
	competency.competency_name = "Create and share a spreadsheet"
	competency.domain = OTHER_DOMAIN
	competency.insert(ignore_permissions=True)

	activity = frappe.new_doc("Sparsh Activity")
	activity.activity_id = OTHER_ACTIVITY
	activity.title = "Share a sheet with a colleague"
	activity.competency = OTHER_COMPETENCY
	activity.activity_type = "Knowledge check"
	activity.instruction = "Which menu shares a spreadsheet with another person?"
	activity.version = 1
	activity.evaluation_mode = "Deterministic"
	activity.expected_response = "share"
	activity.hints = "Look at the top right of the screen."
	activity.insert(ignore_permissions=True)
	frappe.db.commit()

	right = _submit(OTHER_ACTIVITY, "Share")
	_assert(right["outcome"] == "Pass", "The other-domain activity did not evaluate a correct answer")
	_assert(right.get("evidence"), "The other-domain pass produced no evidence")

	wrong = _submit(OTHER_ACTIVITY, "file")
	_assert(wrong["outcome"] == "Fail", "The other-domain activity did not evaluate a wrong answer")
	_assert(wrong["hint"], "The other-domain activity returned no hint")

	state = frappe.db.get_value(
		"Sparsh Mastery State", {"learner": LEARNER, "competency": OTHER_COMPETENCY}, "state"
	)
	_assert(state == "Demonstrated", f"The other-domain competency reached {state}")

	view = dashboard.supervisor_view(competency=OTHER_COMPETENCY)
	_assert(view["learners"] >= 1, "The other-domain competency is invisible to the supervisor view")
	frappe.db.commit()


def check_orchestrator_selects_next():
	"""The engine picks what produces the next evidence, and says why."""
	from sparsh_los import orchestrator, runner

	_reset_competency()

	first = orchestrator.next_experience(COMPETENCY, LEARNER)
	_assert(first["activity"], "No first activity was offered")
	_assert(first["reason"] == orchestrator.PRACTICE, f"First activity offered as {first['reason']}")

	# A critical error outranks sequence: the learner is sent back, not forward.
	_new_evidence(ACTIVITY_1, "Fail", critical_error=1)
	blocked = orchestrator.next_experience(COMPETENCY, LEARNER)
	_assert(
		blocked["reason"] == orchestrator.REMEDIATION,
		f"After a critical error the reason was {blocked['reason']}",
	)
	_assert(blocked["activity"] == ACTIVITY_1, "Remediation did not return to the activity that failed")

	# A later pass must NOT clear a safety error on its own.
	_new_evidence(ACTIVITY_1, "Pass")
	still_blocked = orchestrator.next_experience(COMPETENCY, LEARNER)
	_assert(
		still_blocked["reason"] == orchestrator.REMEDIATION,
		"A later pass silently cleared a critical safety error",
	)

	# Prerequisites block before anything else is considered.
	competency = frappe.get_doc("Sparsh Competency", COMPETENCY_2)
	competency.append("prerequisites", {"prerequisite": COMPETENCY})
	competency.save(ignore_permissions=True)
	_reset_competency()

	gated = orchestrator.next_experience(COMPETENCY_2, LEARNER)
	_assert(
		gated["reason"] == orchestrator.PREREQUISITE,
		f"An unmet prerequisite gave reason {gated['reason']}",
	)
	_assert(COMPETENCY in gated["blocked_by"], "The blocking prerequisite was not named")
	frappe.db.commit()


def check_only_review_clears_critical_error():
	"""A safety error is cleared by a reviewer, never by performing well afterwards."""
	_reset_competency()

	# The reviewer cannot be the learner, so this runs against a separate account.
	_make_learner(TEST_LEARNER)
	frappe.db.commit()
	critical = _new_evidence(ACTIVITY_1, "Fail", critical_error=1, learner=TEST_LEARNER)
	_new_evidence(ACTIVITY_1, "Pass", learner=TEST_LEARNER)
	_new_evidence(ACTIVITY_2, "Pass", learner=TEST_LEARNER)
	_assert(
		_state(TEST_LEARNER) == "Practising",
		f"Two independent passes lifted a standing critical error to {_state(TEST_LEARNER)}",
	)

	def certify():
		doc = frappe.new_doc("Sparsh Certification Record")
		doc.learner = TEST_LEARNER
		doc.competency = COMPETENCY
		doc.certification_status = "Full"
		doc.insert(ignore_permissions=True)

	_raises(certify, "Certification was allowed while a critical error stood")

	review = frappe.new_doc("Sparsh Human Review")
	review.evidence = critical.name
	review.review_status = "Approved"
	review.clears_critical_error = 1
	review.reviewer_comments = "Remediation observed."
	review.insert(ignore_permissions=True)
	review.submit()

	critical.reload()
	_assert(critical.critical_error_cleared == 1, "The review did not clear the critical error")
	_assert(
		_state(TEST_LEARNER) in ("Demonstrated", "Mastered"),
		f"After a reviewed clearance the state is {_state(TEST_LEARNER)}",
	)
	frappe.db.commit()


def check_certification_suspended_on_regression():
	"""A certificate does not outlive the evidence that justified it."""
	_reset_competency()

	_new_evidence(ACTIVITY_1, "Pass")
	_new_evidence(ACTIVITY_2, "Pass")
	_assert(_state() == "Mastered", f"Expected Mastered, got {_state()}")

	certificate = frappe.new_doc("Sparsh Certification Record")
	certificate.learner = LEARNER
	certificate.competency = COMPETENCY
	certificate.certification_status = "Full"
	certificate.insert(ignore_permissions=True)
	certificate.submit()
	_assert(certificate.certification_state == "Active", "A new certificate is not Active")

	_new_evidence(ACTIVITY_1, "Fail", critical_error=1)

	certificate.reload()
	_assert(
		certificate.certification_state == "Suspended",
		f"After a critical error the certificate is {certificate.certification_state}",
	)
	frappe.db.commit()


def check_activityless_evidence_cannot_demonstrate():
	"""Evidence with no activity carries no provenance and proves nothing."""
	_reset_competency()

	# The invalid record is refused outright now, rather than created and ignored.
	_raises(
		lambda: _new_evidence(None, "Pass", submit=False),
		"An unaided pass with no activity was accepted",
	)
	_assert(
		_state() not in ("Demonstrated", "Mastered"),
		f"Evidence with no activity reached {_state()}",
	)
	frappe.db.commit()


def check_identifiers_are_refused():
	"""Training records must not carry patient identifiers."""
	from sparsh_los import escalation

	def mrn_in_response():
		attempt = frappe.new_doc("Sparsh Attempt")
		attempt.learner = LEARNER
		attempt.activity = ACTIVITY_1
		attempt.hint_level_used = 0
		attempt.response = "Discussed with WS123456 about diet"
		attempt.insert(ignore_permissions=True)

	_raises(mrn_in_response, "An MRN-shaped identifier was accepted in an attempt")

	def aadhaar_in_question():
		escalation.raise_question("Caregiver quoted 123456789012, what do I do?", activity=ACTIVITY_1)

	_raises(aadhaar_in_question, "A 12-digit identifier was accepted in an escalation")

	# The grouped and separated forms people actually write.
	for text, label in (
		("Their number is WS-12345", "a hyphenated MRN"),
		("Aadhaar 1234 5678 9012 was quoted", "a spaced Aadhaar number"),
		("Contact them on 9876543210", "a mobile number"),
		("They emailed caregiver@example.com", "an email address"),
	):
		def attempt_with(value=text):
			doc = frappe.new_doc("Sparsh Attempt")
			doc.learner = LEARNER
			doc.activity = ACTIVITY_1
			doc.hint_level_used = 0
			doc.response = value
			doc.insert(ignore_permissions=True)

		_raises(attempt_with, f"{label} was accepted")

	# Ordinary clinical prose must still be accepted.
	attempt = frappe.new_doc("Sparsh Attempt")
	attempt.learner = LEARNER
	attempt.activity = ACTIVITY_1
	attempt.hint_level_used = 0
	attempt.response = "BP was 150/90 and waist 102 cm, so I would assign level 2"
	attempt.insert(ignore_permissions=True)
	_assert(attempt.name, "Ordinary clinical prose was rejected")
	frappe.db.commit()


def check_certification_readiness():
	"""The platform says why someone is not ready, not merely that they failed."""
	from sparsh_los import certification

	_reset_competency()

	early = certification.readiness(COMPETENCY, LEARNER)
	_assert(
		early["verdict"] == certification.INSUFFICIENT,
		f"With no evidence the verdict was {early['verdict']}",
	)
	_assert(early["reason"], "No reason was given for a not-ready verdict")

	_new_evidence(ACTIVITY_1, "Pass")
	ready = certification.readiness(COMPETENCY, LEARNER)
	_assert(ready["verdict"] == certification.READY, f"After a pass the verdict was {ready['verdict']}")
	_assert(ready["independent_passes"] == 1, "The independent pass was not counted")
	_assert(ACTIVITY_1 in ready["distinct_activities"], "The activity was not listed")

	_new_evidence(ACTIVITY_1, "Fail", critical_error=1)
	blocked = certification.readiness(COMPETENCY, LEARNER)
	_assert(
		blocked["verdict"] == certification.BLOCKED,
		f"With a standing critical error the verdict was {blocked['verdict']}",
	)
	_assert(blocked["blocking_evidence"], "The blocking evidence was not named")

	cohort = certification.cohort_readiness(COMPETENCY)
	_assert(cohort["counts"][certification.BLOCKED] >= 1, "The cohort view did not count the block")
	frappe.db.commit()


def check_whitelisted_reads_are_scoped():
	"""A learner cannot read another learner's record through a whitelisted method."""
	from sparsh_los import certification, dashboard, orchestrator

	_make_learner(TEST_LEARNER)
	frappe.db.commit()

	original_user = frappe.session.user
	try:
		frappe.set_user(TEST_LEARNER)

		for call, label in (
			(lambda: dashboard.learner_view(LEARNER), "dashboard.learner_view"),
			(lambda: orchestrator.next_experience(COMPETENCY, LEARNER), "orchestrator.next_experience"),
			(lambda: certification.readiness(COMPETENCY, LEARNER), "certification.readiness"),
			(lambda: dashboard.supervisor_view(), "dashboard.supervisor_view"),
			(lambda: dashboard.competency_heatmap(), "dashboard.competency_heatmap"),
			(lambda: certification.cohort_readiness(COMPETENCY), "certification.cohort_readiness"),
		):
			try:
				call()
				raise AssertionError(f"{label} was readable by a learner")
			except frappe.PermissionError:
				pass

		# Their own record stays readable.
		own = dashboard.learner_view()
		_assert(own["learner"] == TEST_LEARNER, "A learner could not read their own record")
	finally:
		frappe.set_user(original_user)

	frappe.db.commit()


def check_refresher_time_based():
	"""Competence expires on time when the competency says it does."""
	from sparsh_los import refresher
	from sparsh_los.mastery import REFRESH_DUE

	_reset_competency()
	_delete_all("Sparsh Refresher Assignment", {"competency": COMPETENCY})

	frappe.db.set_value("Sparsh Competency", COMPETENCY, "refresh_interval_days", 90)
	evidence = _new_evidence(ACTIVITY_1, "Pass")
	_assert(_state() == "Demonstrated", f"Expected Demonstrated, got {_state()}")

	# Age the demonstration past the interval.
	old = frappe.utils.add_days(frappe.utils.now_datetime(), -200)
	frappe.db.set_value("Sparsh Evidence", evidence.name, "recorded_at", old)
	frappe.db.set_value("Sparsh Evidence", evidence.name, "creation", old)
	frappe.db.commit()

	from sparsh_los.mastery import derive_state

	_assert(
		derive_state(LEARNER, COMPETENCY) == REFRESH_DUE,
		f"An aged demonstration derived {derive_state(LEARNER, COMPETENCY)}",
	)

	assigned = refresher.evaluate_time_based()
	_assert(assigned, "No refresher was assigned for an aged demonstration")

	# Idempotent: a second run does not pile up duplicates.
	again = refresher.evaluate_time_based()
	_assert(not again, "A second run duplicated the refresher assignment")

	frappe.db.set_value("Sparsh Competency", COMPETENCY, "refresh_interval_days", 0)
	frappe.db.commit()


def check_refresher_on_rule_change():
	"""Superseding a rule schedules everyone judged against the old one."""
	from sparsh_los import refresher

	_reset_competency()
	_delete_all("Sparsh Refresher Assignment", {"competency": COMPETENCY})
	_delete_all("Sparsh Source of Truth Rule", {"rule_id": RULE_ID})
	frappe.db.commit()

	rule_v1 = _new_rule(1)
	competency = frappe.get_doc("Sparsh Competency", COMPETENCY)
	competency.set("linked_rules", [])
	competency.append("linked_rules", {"rule": rule_v1.name})
	competency.save(ignore_permissions=True)

	_new_evidence(ACTIVITY_1, "Pass")
	_assert(_state() == "Demonstrated", f"Expected Demonstrated, got {_state()}")

	_new_rule(2, supersedes=rule_v1.name)

	assignments = frappe.get_all(
		"Sparsh Refresher Assignment",
		filters={"competency": COMPETENCY, "trigger_reason": refresher.RULE_CHANGED},
		fields=["learner", "status"],
	)
	_assert(assignments, "Superseding a rule assigned no refresher")
	_assert(
		any(a.learner == LEARNER for a in assignments),
		"The learner judged against the old rule was not scheduled",
	)

	# Scheduling the work is not the same as marking the demonstration stale. Until
	# this held, the learner stayed Demonstrated and kept a standing certificate
	# against a rule the programme had already replaced — the time-based trigger
	# persisted that regression from the start, this one did not.
	_assert(
		_state() == "Refresh Due",
		f"After the rule changed underneath them the learner is {_state()}",
	)

	# The engine must be able to close it. Fresh evidence recorded after the assignment
	# is what answers a refresher; before this held, nothing outside the desk ever set
	# a row to Completed, so Refresh Due -- and the suspended certificate with it --
	# lasted until a human edited the row by hand.
	_new_evidence(ACTIVITY_2, "Pass")
	_assert(
		_state() in ("Demonstrated", "Mastered"),
		f"Fresh evidence did not close the refresher; learner is {_state()}",
	)
	_assert(
		not frappe.db.exists(
			"Sparsh Refresher Assignment",
			{"learner": LEARNER, "competency": COMPETENCY, "status": "Assigned"},
		),
		"The refresher stayed open after the learner answered it",
	)

	# And an explicit completion by a reviewer gives the competency back too, which is
	# the controller's on_update edge rather than the evidence-driven one above.
	reopened = refresher.assign(
		LEARNER, COMPETENCY, refresher.RULE_CHANGED, "Verification: reopened by hand."
	)
	_assert(reopened, "Could not reopen a refresher to test explicit completion")
	_assert(
		_state() == "Refresh Due",
		f"A newly assigned refresher did not hold the learner: {_state()}",
	)
	done = frappe.get_doc("Sparsh Refresher Assignment", reopened)
	done.status = "Completed"
	done.save(ignore_permissions=True)
	_assert(
		_state() in ("Demonstrated", "Mastered"),
		f"Completing the refresher left the learner at {_state()}",
	)
	frappe.db.commit()


def check_matrix_loads_as_draft():
	"""The programme matrix is loaded as Draft and nothing arrives pre-validated."""
	from sparsh_los import seed

	created, skipped = seed.load_matrix()
	_assert(created or skipped, "The matrix loader produced nothing")

	status = seed.matrix_status()
	_assert(status["total"] >= 17, f"Only {status['total']} matrix rules are present")
	_assert(
		status["by_status"].get("Validated", 0) == 0,
		"A matrix rule arrived already Validated",
	)
	_assert(
		status["unvalidated_safety_critical"],
		"No safety-critical rule is flagged as awaiting validation",
	)
	_assert(
		not status["ready_to_automate"],
		f"Rules are marked ready to automate before validation: {status['ready_to_automate']}",
	)

	# Re-running must not duplicate.
	before = frappe.db.count("Sparsh Source of Truth Rule")
	seed.load_matrix()
	_assert(
		frappe.db.count("Sparsh Source of Truth Rule") == before,
		"Re-running the matrix loader duplicated rules",
	)
	frappe.db.commit()


def check_clearance_must_be_backed_by_review():
	"""The cleared flag is worthless unless a real approved review stands behind it."""
	_reset_competency()
	evidence = _new_evidence(ACTIVITY_1, "Fail", critical_error=1)

	def forge_clearance():
		evidence.db_set("critical_error_cleared", 1)
		evidence.reload()
		evidence.save(ignore_permissions=True)

	_raises(forge_clearance, "A clearance without a review was accepted", expect="review")

	# The forgery went in through db_set, which bypasses validate. Proving the save
	# objected says nothing about whether the flag is still sitting in the row.
	evidence.reload()
	_assert(
		not evidence.critical_error_cleared,
		"A forged clearance survived in the database after the save was refused",
	)
	from sparsh_los.mastery import has_blocking_critical_error

	_assert(
		has_blocking_critical_error(LEARNER, COMPETENCY),
		"A forged clearance lifted the block even though the save was refused",
	)

	# And a safety error cannot be made to vanish by cancelling the record.
	evidence.reload()
	frappe.db.set_value("Sparsh Evidence", evidence.name, "critical_error_cleared", 0)
	evidence.reload()
	_raises(
		lambda: evidence.cancel(),
		"Evidence carrying an unresolved critical error was cancelled",
	)
	frappe.db.commit()


def check_cancelling_review_restores_block():
	"""Withdrawing a clearance restores the block it lifted."""
	_reset_competency()
	_make_learner(TEST_LEARNER)
	frappe.db.commit()
	critical = _new_evidence(ACTIVITY_1, "Fail", critical_error=1, learner=TEST_LEARNER)
	_new_evidence(ACTIVITY_1, "Pass", learner=TEST_LEARNER)
	_assert(_state(TEST_LEARNER) == "Practising", f"Expected Practising, got {_state(TEST_LEARNER)}")

	review = frappe.new_doc("Sparsh Human Review")
	review.evidence = critical.name
	review.review_status = "Approved"
	review.clears_critical_error = 1
	review.insert(ignore_permissions=True)
	review.submit()
	_assert(
		_state(TEST_LEARNER) == "Demonstrated",
		f"After clearance expected Demonstrated, got {_state(TEST_LEARNER)}",
	)

	review.cancel()
	critical.reload()
	_assert(not critical.critical_error_cleared, "Cancelling the review left the clearance in place")
	_assert(
		_state(TEST_LEARNER) == "Practising",
		f"Cancelling the review left the state at {_state(TEST_LEARNER)}",
	)
	frappe.db.commit()


def check_learner_cannot_read_answer_key():
	"""The person being assessed cannot read the answer."""
	_make_learner(TEST_LEARNER)
	frappe.db.commit()

	original_user = frappe.session.user
	try:
		frappe.set_user(TEST_LEARNER)
		doc = frappe.get_doc("Sparsh Activity", ACTIVITY_1)
		# This is what the REST and desk layers do before handing a document to a user.
		doc.apply_fieldlevel_read_permissions()
		_assert(not doc.get("expected_response"), "A learner could read the expected response")
		_assert(not doc.get("critical_markers"), "A learner could read the critical-error markers")

		levels = frappe.get_meta("Sparsh Activity").get_permlevel_access("read", user=TEST_LEARNER)
		_assert(1 not in levels, f"A learner holds permlevel-1 read on Activity: {levels}")
	finally:
		frappe.set_user(original_user)
	frappe.db.commit()


def check_critical_response_reaches_a_person():
	"""A critical result is escalated, not merely logged."""
	from sparsh_los import runner

	_reset_competency()
	_delete_all("Sparsh Escalation Question", {"learner": LEARNER})
	frappe.db.commit()

	result = _submit(ACTIVITY_1, "I would tell them to stop the medicine")
	_assert(result["critical_error"] == 1, "A critical response was not flagged")
	_assert(result.get("escalation"), "A critical response raised no escalation")

	question = frappe.get_doc("Sparsh Escalation Question", result["escalation"])
	_assert(question.status == "Open", "The automatic escalation is not open")
	_assert(question.escalation_reason == "Safety critical", "The escalation reason is wrong")

	# A directly negated mention is not unsafe.
	negated = _submit(ACTIVITY_1, "I would tell them to not stop the medicine")
	_assert(
		not negated["critical_error"],
		"A negated mention of a critical marker was flagged as unsafe",
	)

	# Punctuation does not smuggle an unsafe answer past the marker.
	punctuated = _submit(ACTIVITY_1, "Simple: stop the medicine.")
	_assert(punctuated["critical_error"] == 1, "Punctuation defeated the critical marker")

	# And a distant negation is not a negation of this phrase.
	distant = _submit(ACTIVITY_1, "Do not hesitate to stop the medicine")
	_assert(
		distant["critical_error"] == 1,
		"A distant negation wrongly suppressed a critical marker",
	)
	frappe.db.commit()


def check_evidence_cannot_contradict_the_attempt():
	"""Evidence cannot upgrade a failed or assisted attempt into a clean pass."""
	_reset_competency()
	attempt = _new_attempt(None, outcome="Fail")

	def upgrade():
		doc = frappe.new_doc("Sparsh Evidence")
		doc.learner = LEARNER
		doc.competency = COMPETENCY
		doc.activity = ACTIVITY_1
		doc.activity_version = 1
		doc.attempt = attempt.name
		doc.outcome = "Pass"
		doc.assistance_level = 0
		doc.insert(ignore_permissions=True)

	_raises(upgrade, "Evidence upgraded a failed attempt to a pass")

	assisted = _new_attempt(None, outcome="Pass", hint_level=3)

	def understate_help():
		doc = frappe.new_doc("Sparsh Evidence")
		doc.learner = LEARNER
		doc.competency = COMPETENCY
		doc.activity = ACTIVITY_1
		doc.activity_version = 1
		doc.attempt = assisted.name
		doc.outcome = "Pass"
		doc.assistance_level = 0
		doc.insert(ignore_permissions=True)

	_raises(understate_help, "Evidence claimed less assistance than the attempt recorded")

	# Omitting the activity used to escape both the provenance rule and the
	# competency check; it is inherited from the attempt instead.
	sound = _new_attempt(None, outcome="Pass")
	frappe.db.set_value("Sparsh Attempt", sound.name, "activity", ACTIVITY_1)
	inherited = frappe.new_doc("Sparsh Evidence")
	inherited.learner = LEARNER
	inherited.competency = COMPETENCY
	inherited.activity_version = 1
	inherited.attempt = sound.name
	inherited.outcome = "Pass"
	inherited.assistance_level = 0
	inherited.insert(ignore_permissions=True)
	_assert(
		inherited.activity == ACTIVITY_1,
		f"Evidence did not inherit the attempt's activity, it has {inherited.activity}",
	)
	frappe.db.commit()


def check_runner_records_the_governing_rule():
	"""An attempt identifies the rule it was judged under."""
	from sparsh_los import runner

	_reset_competency()
	_delete_all("Sparsh Source of Truth Rule", {"rule_id": RULE_ID})
	frappe.db.commit()

	rule = _new_rule(1)
	competency = frappe.get_doc("Sparsh Competency", COMPETENCY)
	competency.set("linked_rules", [])
	competency.append("linked_rules", {"rule": rule.name})
	competency.save(ignore_permissions=True)
	frappe.db.commit()

	result = _submit(ACTIVITY_1, "level two")
	attempt = frappe.get_doc("Sparsh Attempt", result["attempt"])
	_assert(attempt.rule == rule.name, f"The attempt recorded rule {attempt.rule}")
	_assert(attempt.rule_version == 1, f"The attempt recorded version {attempt.rule_version}")
	frappe.db.commit()


def check_human_review_activity_completes():
	"""A reviewed activity has a path from recorded attempt to evidence."""
	from sparsh_los import review, runner

	_reset_competency()

	activity = frappe.get_doc("Sparsh Activity", ACTIVITY_2)
	activity.evaluation_mode = "Human review"
	activity.save(ignore_permissions=True)
	frappe.db.commit()

	# The attempt must belong to somebody other than the reviewer running this check.
	_make_learner(TEST_LEARNER)
	frappe.db.commit()

	original_user = frappe.session.user
	try:
		frappe.set_user(TEST_LEARNER)
		result = runner.submit(ACTIVITY_2, "I would explore what makes this hard for them first.")
	finally:
		frappe.set_user(original_user)

	_assert(result["outcome"] == "Not Evaluated", "A reviewed activity was auto-graded")
	_assert("evidence" not in result, "A reviewed activity produced evidence without a reviewer")

	waiting = review.pending(competency=COMPETENCY)
	_assert(
		any(w["name"] == result["attempt"] for w in waiting),
		"The recorded attempt is not in the reviewer's queue",
	)

	recorded = review.record_evidence(result["attempt"], "Pass", assistance_level=0)
	_assert(recorded["evidence"], "The reviewer could not record evidence")
	_assert(recorded["state"] == "Demonstrated", f"After review the state is {recorded['state']}")
	_assert(
		frappe.db.get_value("Sparsh Attempt", result["attempt"], "outcome") == "Not Evaluated",
		"Recording evidence rewrote the attempt, which is meant to be immutable",
	)

	# It leaves the queue, and cannot be recorded twice.
	still_waiting = review.pending(competency=COMPETENCY)
	_assert(
		not any(w["name"] == result["attempt"] for w in still_waiting),
		"A reviewed attempt is still queued",
	)
	_raises(
		lambda: review.record_evidence(result["attempt"], "Pass"),
		"The same attempt was turned into evidence twice",
	)

	activity.reload()
	activity.evaluation_mode = "Deterministic"
	activity.save(ignore_permissions=True)
	frappe.db.commit()


def check_review_is_not_open_to_learners():
	"""Recording evidence is a reviewer action."""
	from sparsh_los import review

	_make_learner(TEST_LEARNER)
	frappe.db.commit()

	original_user = frappe.session.user
	try:
		frappe.set_user(TEST_LEARNER)
		for call, label in (
			(lambda: review.pending(), "review.pending"),
			(lambda: review.record_evidence("nonexistent", "Pass"), "review.record_evidence"),
		):
			try:
				call()
				raise AssertionError(f"{label} was callable by a learner")
			except frappe.PermissionError:
				pass
	finally:
		frappe.set_user(original_user)
	frappe.db.commit()


def check_one_standing_certification():
	"""The ledger can answer 'is this person certified right now?'."""
	from sparsh_los.sparsh_los.doctype.sparsh_certification_record import (
		sparsh_certification_record as cert,
	)

	_reset_competency()
	_new_evidence(ACTIVITY_1, "Pass")
	_assert(_state() == "Demonstrated", f"Expected Demonstrated, got {_state()}")

	first = frappe.new_doc("Sparsh Certification Record")
	first.learner = LEARNER
	first.competency = COMPETENCY
	first.certification_status = "Full"
	first.insert(ignore_permissions=True)
	first.submit()

	standing = cert.current(LEARNER, COMPETENCY)
	_assert(standing and standing["name"] == first.name, "The ledger lost the standing certification")

	# A second certification cannot stand alongside the first.
	def duplicate():
		second = frappe.new_doc("Sparsh Certification Record")
		second.learner = LEARNER
		second.competency = COMPETENCY
		second.certification_status = "Full"
		second.insert(ignore_permissions=True)
		second.submit()

	_raises(duplicate, "Two certifications stand for the same competency")

	# A revocation must name what it withdraws.
	def unattached_revocation():
		bad = frappe.new_doc("Sparsh Certification Record")
		bad.learner = LEARNER
		bad.competency = COMPETENCY
		bad.certification_status = "Revoked"
		bad.insert(ignore_permissions=True)

	_raises(unattached_revocation, "A revocation naming no certification was accepted")

	revocation = frappe.new_doc("Sparsh Certification Record")
	revocation.learner = LEARNER
	revocation.competency = COMPETENCY
	revocation.certification_status = "Revoked"
	revocation.revokes = first.name
	revocation.insert(ignore_permissions=True)
	revocation.submit()

	first.reload()
	_assert(first.certification_state == "Revoked", f"The revoked record is {first.certification_state}")
	_assert(
		cert.current(LEARNER, COMPETENCY) is None,
		"A revoked certification still reads as standing",
	)

	# Evidence cannot un-revoke a governance decision.
	_new_evidence(ACTIVITY_2, "Pass")
	first.reload()
	_assert(first.certification_state == "Revoked", "New evidence resurrected a revoked certification")
	frappe.db.commit()


def check_practice_page_builds():
	"""The learner page assembles from the same read models as everything else."""
	from sparsh_los.www import practice

	_reset_competency()
	_new_evidence(ACTIVITY_1, "Pass")
	frappe.db.commit()

	context = frappe._dict()
	original_user = frappe.session.user
	try:
		frappe.set_user(LEARNER)
		practice.get_context(context)
	finally:
		frappe.set_user(original_user)

	_assert(context.learner == LEARNER, "The page named the wrong learner")
	_assert(context.view["competencies"], "The page shows no competencies")
	_assert(
		any(c["competency"] == COMPETENCY for c in context.view["competencies"]),
		"The page is missing the competency just demonstrated",
	)

	# The page must offer the next thing to do. Wrapping this in "if next_up" meant
	# a page that offered nothing passed the check.
	_assert(context.next_up, "The page offered the learner nothing to do")
	_assert(context.next_up.get("activity"), "next_up carries no activity")
	_assert(context.next_up.get("instruction"), "next_up carries no instruction to show")
	_assert("reason" in context.next_up, "next_up does not say why")

	# A guest gets nothing.
	original_user = frappe.session.user
	try:
		frappe.set_user("Guest")
		try:
			practice.get_context(frappe._dict())
			raise AssertionError("The practice page rendered for Guest")
		except frappe.PermissionError:
			pass
	finally:
		frappe.set_user(original_user)
	frappe.db.commit()


def check_constraints_are_in_the_database():
	"""The invariants that lose races are enforced where they cannot be raced."""

	def unique_indexes(table):
		rows = frappe.db.sql(f"show index from `tab{table}`", as_dict=1)
		return {r["Key_name"] for r in rows if r["Non_unique"] == 0}

	_assert(
		"unique_learner_competency" in unique_indexes("Sparsh Mastery State"),
		"Mastery State has no unique index on (learner, competency)",
	)
	_assert(
		"unique_evidence_per_attempt" in unique_indexes("Sparsh Evidence"),
		"Evidence has no unique index on attempt",
	)
	_assert(
		"unique_standing_certification" in unique_indexes("Sparsh Certification Record"),
		"Certification Record has no unique index on standing_key",
	)

	# And the constraint actually bites: a second evidence row for one attempt fails
	# at the database, not merely at the controller.
	_reset_competency()
	attempt = _new_attempt(None, outcome="Pass")
	first = _new_evidence(ACTIVITY_1, "Pass", submit=False)
	first.attempt = attempt.name
	first.save(ignore_permissions=True)
	first.submit()
	# Committed before the duplicate is attempted: the rollback that follows the
	# constraint violation would otherwise take this row with it, and the survivor
	# count below would be measuring the rollback rather than the constraint.
	frappe.db.commit()

	def duplicate_evidence():
		second = frappe.new_doc("Sparsh Evidence")
		second.learner = LEARNER
		second.competency = COMPETENCY
		second.activity = ACTIVITY_1
		second.activity_version = 1
		second.attempt = attempt.name
		second.outcome = "Pass"
		second.assistance_level = 0
		# Bypass the controller check to prove the database is the backstop.
		second.flags.ignore_validate = True
		second.insert(ignore_permissions=True)

	# The exception has to be the duplicate-key one. Accepting any exception meant a
	# missing table, a link-validation failure or a controller error all read as proof
	# that the database constraint held.
	raised = None
	try:
		duplicate_evidence()
	except Exception as exc:  # noqa: BLE001
		raised = exc
		frappe.db.rollback()
	_assert(raised is not None, "The database accepted two evidence rows for one attempt")
	_assert(
		isinstance(raised, frappe.exceptions.UniqueValidationError)
		or "1062" in str(raised),
		f"The insert failed, but not on the unique constraint: {type(raised).__name__}: {raised}",
	)

	# And exactly one row survived, which is the thing the constraint exists to ensure.
	surviving = frappe.db.count("Sparsh Evidence", {"attempt": attempt.name, "docstatus": ("<", 2)})
	_assert(surviving == 1, f"{surviving} evidence rows exist for one attempt, expected 1")
	frappe.db.commit()


def check_rule_refresher_uses_provenance():
	"""A rule change schedules the people judged under that rule."""
	from sparsh_los import refresher

	_reset_competency()
	_delete_all("Sparsh Refresher Assignment", {"competency": COMPETENCY})
	_delete_all("Sparsh Source of Truth Rule", {"rule_id": RULE_ID})
	frappe.db.commit()

	rule_v1 = _new_rule(1)
	competency = frappe.get_doc("Sparsh Competency", COMPETENCY)
	competency.set("linked_rules", [])
	competency.append("linked_rules", {"rule": rule_v1.name})
	competency.save(ignore_permissions=True)

	# Two learners hold the competency; only one was judged under this rule.
	_make_learner(TEST_LEARNER)
	_make_learner(OTHER_LEARNER)
	frappe.db.commit()

	judged = _new_attempt(rule_v1.name, outcome="Pass")
	frappe.db.set_value("Sparsh Attempt", judged.name, "learner", TEST_LEARNER)
	_new_evidence(ACTIVITY_1, "Pass", learner=TEST_LEARNER)
	_new_evidence(ACTIVITY_1, "Pass", learner=OTHER_LEARNER)
	frappe.db.commit()

	_new_rule(2, supersedes=rule_v1.name)

	scheduled = {
		row.learner
		for row in frappe.get_all(
			"Sparsh Refresher Assignment",
			filters={"competency": COMPETENCY, "trigger_reason": refresher.RULE_CHANGED},
			fields=["learner"],
		)
	}
	_assert(TEST_LEARNER in scheduled, "The learner judged under the old rule was not scheduled")
	_assert(
		OTHER_LEARNER not in scheduled,
		"A learner never judged under the rule was scheduled anyway",
	)
	frappe.db.commit()


def check_review_queue_page():
	"""The reviewer's page assembles, and is closed to learners."""
	from sparsh_los.www import queue

	_reset_competency()
	_new_evidence(ACTIVITY_1, "Fail", critical_error=1)
	frappe.db.commit()

	context = frappe._dict()
	queue.get_context(context)
	_assert(context.reviewer == frappe.session.user, "The queue named the wrong reviewer")
	_assert(context.critical, "A standing critical error is missing from the queue")
	_assert("pending_reviews" in context, "The queue has no pending-review list")
	_assert("cohort" in context, "The queue has no cohort summary")

	_make_learner(TEST_LEARNER)
	frappe.db.commit()
	original_user = frappe.session.user
	try:
		frappe.set_user(TEST_LEARNER)
		try:
			queue.get_context(frappe._dict())
			raise AssertionError("The review queue rendered for a learner")
		except frappe.PermissionError:
			pass
	finally:
		frappe.set_user(original_user)
	frappe.db.commit()


def check_case_pack_loads_for_review_only():
	"""The programme's cases load, and not one of them can auto-score anybody."""
	from sparsh_los import seed

	created, skipped = seed.load_case_pack()
	status = seed.case_pack_status()

	_assert(status["loaded"] >= 9, f"Only {status['loaded']} cases loaded")
	_assert(
		status["loaded"] == status["awaiting_human_review"],
		f"Cases are set to auto-score: {status['auto_scored']}",
	)
	_assert(not status["auto_scored"], f"These cases would score without a person: {status['auto_scored']}")

	# The competencies the cases belong to exist.
	for competency_id in ("SSP-RISK", "SSP-PLEDGE", "SSP-SCOPE"):
		_assert(
			frappe.db.exists("Sparsh Competency", competency_id),
			f"Competency {competency_id} was not created",
		)

	# Re-running does not duplicate.
	before = frappe.db.count("Sparsh Activity", {"activity_id": ("like", "SC-%")})
	seed.load_case_pack()
	_assert(
		frappe.db.count("Sparsh Activity", {"activity_id": ("like", "SC-%")}) == before,
		"Re-running the case pack loader duplicated activities",
	)
	frappe.db.commit()


def check_programme_readiness_is_honest():
	"""The readiness report tells the programme owner the truth."""
	from sparsh_los import seed

	seed.load_matrix()
	seed.load_case_pack()
	report = seed.programme_readiness()

	_assert(report["rules_total"] >= 17, f"Only {report['rules_total']} rules are present")
	_assert(
		not report["rules_ready_to_automate"],
		f"Rules are reported ready to automate before validation: {report['rules_ready_to_automate']}",
	)
	_assert(
		report["unvalidated_safety_critical"],
		"No safety-critical rule is reported as awaiting validation",
	)
	_assert(report["blocking"], "The report claims nothing is blocking the pilot")
	_assert(
		any("safety-critical" in line for line in report["blocking"]),
		f"Unvalidated safety rules are not named as blocking: {report['blocking']}",
	)

	# It is a reviewer view.
	_make_learner(TEST_LEARNER)
	frappe.db.commit()
	original_user = frappe.session.user
	try:
		frappe.set_user(TEST_LEARNER)
		try:
			seed.programme_readiness()
			raise AssertionError("A learner could read the programme readiness report")
		except frappe.PermissionError:
			pass
	finally:
		frappe.set_user(original_user)
	frappe.db.commit()


def check_activity_cannot_change_competency():
	"""An activity with evidence against it stays where it is."""
	_reset_competency()
	_new_evidence(ACTIVITY_1, "Pass")
	frappe.db.commit()

	def reassign():
		doc = frappe.get_doc("Sparsh Activity", ACTIVITY_1)
		doc.competency = COMPETENCY_2
		doc.save(ignore_permissions=True)

	_raises(reassign, "An activity with evidence was moved to another competency")

	# An activity with no evidence may still be moved.
	_reset_competency()
	doc = frappe.get_doc("Sparsh Activity", ACTIVITY_2)
	doc.competency = COMPETENCY_2
	doc.save(ignore_permissions=True)
	doc.reload()
	_assert(doc.competency == COMPETENCY_2, "An unused activity could not be moved")
	doc.competency = COMPETENCY
	doc.save(ignore_permissions=True)
	frappe.db.commit()


def check_rejected_evidence_does_not_count():
	"""Evidence a reviewer rejected is not evidence of competence."""
	_reset_competency()

	evidence = _new_evidence(ACTIVITY_1, "Pass", submit=False)
	evidence.human_review_status = "Rejected"
	evidence.save(ignore_permissions=True)
	evidence.submit()

	_assert(
		_state() not in ("Demonstrated", "Mastered"),
		f"A rejected pass reached {_state()}",
	)

	# An approved one does count.
	_new_evidence(ACTIVITY_1, "Pass")
	_assert(_state() == "Demonstrated", f"An approved pass gave {_state()}")
	frappe.db.commit()


def check_events_are_recorded_and_hold_no_content():
	"""The usage log records what happened, never what was said.

	Kept from the start because these events cannot be invented retrospectively —
	nobody can go back and observe what learners did last month. The second assertion
	is the one that makes the log safe to keep: a learner's typed response and an
	activity's answer key must never appear in it.
	"""
	from sparsh_los import events, runner

	_reset_competency()
	_delete_all("Sparsh Event", {"learner": LEARNER})
	frappe.db.commit()

	activity = frappe.get_doc("Sparsh Activity", ACTIVITY_1)
	original_mode, original_expected = activity.evaluation_mode, activity.expected_response
	activity.evaluation_mode = "Deterministic"
	activity.expected_response = "level two"
	activity.save(ignore_permissions=True)
	frappe.db.commit()

	secret = "level two"
	wrong_answer = "zzv unmistakable wrong answer"
	started_at = frappe.utils.now_datetime()
	original_user = frappe.session.user
	try:
		frappe.set_user(LEARNER)
		runner.start(ACTIVITY_1)
	finally:
		frappe.set_user(original_user)
	_submit(ACTIVITY_1, wrong_answer)
	_submit(ACTIVITY_1, secret)
	frappe.db.commit()

	# The escalation path is the one emit whose detail begins as a whitelisted
	# argument, so it is the only real risk surface -- and it was not exercised.
	from sparsh_los import escalation

	original_user = frappe.session.user
	try:
		frappe.set_user(LEARNER)
		escalation.raise_question(
			f"A question mentioning {secret} and {wrong_answer}", activity=ACTIVITY_1
		)
	finally:
		frappe.set_user(original_user)
	frappe.db.commit()

	# Every event written during this check, not only the ones that named a learner:
	# every emit parameter is optional, so a leak that omitted `learner` would have
	# been invisible to a filtered scan. Every field, not only `detail`, for the
	# same reason.
	rows = frappe.get_all(
		"Sparsh Event",
		filters={"creation": (">", started_at)},
		fields=["event_type", "detail", "learner", "competency", "activity", "reference_name"],
	)
	seen = {r.event_type for r in rows}
	for required in (
		events.ACTIVITY_STARTED,
		events.ACTIVITY_COMPLETED,
		events.HINT_SHOWN,
		events.MASTERY_STATE_CHANGED,
	):
		_assert(required in seen, f"No {required} event was recorded; saw {sorted(seen)}")

	_assert(
		events.ESCALATION_OPENED in {r.event_type for r in rows},
		"Raising a question recorded no escalation event",
	)

	# `escalation_reason` is the one detail string that starts as a whitelisted
	# argument. The Select refuses an unexpected value at insert, so the insert path
	# cannot reach the fallback -- which is precisely what the fallback is for: it is
	# what holds if that field is ever widened to Data. Tested where it lives, because
	# testing it through the insert would only re-prove the Select.
	raised_q = frappe.get_all(
		"Sparsh Escalation Question",
		filters={"learner": LEARNER},
		fields=["name"],
		order_by="creation desc",
		limit=1,
	)
	_assert(raised_q, "No escalation question to test the reason fallback against")
	widened = frappe.get_doc("Sparsh Escalation Question", raised_q[0].name)
	widened.escalation_reason = f"<widened> {wrong_answer}"
	escalation._emit_opened(widened)
	leaked = frappe.get_all(
		"Sparsh Event",
		filters={"event_type": events.ESCALATION_OPENED, "creation": (">", started_at)},
		pluck="detail",
	)
	_assert(
		all(wrong_answer not in (d or "").lower() for d in leaked),
		f"A widened escalation reason carried learner text into the log: {leaked}",
	)

	# Neither the learner's own words nor the answer key reach the log, in any field.
	for row in rows:
		blob = " ".join(str(v or "") for v in row.values()).lower()
		_assert(wrong_answer not in blob, f"An event carried the learner's response: {row}")
		_assert(secret not in blob, f"An event carried the answer key: {row}")

	# And the log is a log: not writable by hand, even by a System Manager.
	def hand_written():
		doc = frappe.new_doc("Sparsh Event")
		doc.event_type = "forged"
		doc.occurred_at = frappe.utils.now_datetime()
		doc.learner = LEARNER
		doc.insert(ignore_permissions=True)

	_raises(hand_written, "An event could be written by hand", expect="engine")

	# And deleting one outside maintenance. _delete_all sets the maintenance flag, so
	# the on_trash guard was only ever exercised in the mode that permits it --
	# removing the guard entirely would have failed nothing.
	existing = frappe.get_all("Sparsh Event", limit=1, pluck="name")
	_assert(existing, "No event to test deletion against")
	_raises(
		lambda: frappe.delete_doc(
			"Sparsh Event", existing[0], force=True, ignore_permissions=True
		),
		"An event could be deleted outside maintenance",
		expect="log",
	)

	# Fixture restored to what it was, not to a guess. Hardcoding "Human review" here
	# left every later check on a different baseline than every earlier one, under a
	# comment claiming a restoration it did not perform.
	activity.reload()
	activity.evaluation_mode = original_mode
	activity.expected_response = original_expected
	activity.save(ignore_permissions=True)
	frappe.db.commit()


def check_superseded_resource_does_not_rewrite_history():
	"""Replacing a learning resource marks readers Refresh Due; it rewrites nothing.

	The programme owner's requirement in full: when approved content changes
	materially, earlier evidence must stay historically interpretable and the
	competency is marked Refresh Due instead of prior evidence or certification being
	overwritten. Both halves are asserted here — the staleness, and the untouched
	history.
	"""
	from sparsh_los import refresher

	_reset_competency()
	_delete_all("Sparsh Competency Resource Link", {"resource": ("like", PREFIX + "%")})

	def _resource(version, supersedes=None, status="Current"):
		name = f"{PREFIX}RES-v{version}"
		if frappe.db.exists("Sparsh Learning Resource", name):
			frappe.delete_doc(
				"Sparsh Learning Resource", name, force=True, ignore_permissions=True
			)
		doc = frappe.new_doc("Sparsh Learning Resource")
		doc.resource_id = PREFIX + "RES"
		doc.version = version
		doc.title = f"Verification resource v{version}"
		doc.resource_type = "Manual section"
		doc.status = "Draft" if supersedes else status
		doc.supersedes = supersedes
		doc.insert(ignore_permissions=True)
		return doc

	first = _resource(1)
	competency = frappe.get_doc("Sparsh Competency", COMPETENCY)
	competency.set("learning_resources", [])
	competency.append("learning_resources", {"resource": first.name, "relevance": "Primary"})
	competency.save(ignore_permissions=True)
	frappe.db.commit()

	_new_evidence(ACTIVITY_1, "Pass")
	_assert(_state() == "Demonstrated", f"Expected Demonstrated, got {_state()}")
	# Content, not just names. Comparing name lists alone would pass after a regression
	# that cancelled every row, flipped its outcome, or marked it Rejected -- which is
	# precisely what "rewriting history" means.
	evidence_fields = ["name", "outcome", "docstatus", "assistance_level", "human_review_status"]
	evidence_before = sorted(
		frappe.get_all(
			"Sparsh Evidence",
			filters={"learner": LEARNER, "competency": COMPETENCY},
			fields=evidence_fields,
		),
		key=lambda r: r["name"],
	)

	# And the certification half of the requirement, which had no fixture at all: the
	# certificate must be suspended, not deleted or rewritten.
	certificate = frappe.new_doc("Sparsh Certification Record")
	certificate.learner = LEARNER
	certificate.competency = COMPETENCY
	certificate.certification_status = "Full"
	certificate.insert(ignore_permissions=True)
	certificate.submit()
	frappe.db.commit()

	try:
		second = _resource(2, supersedes=first.name)
		second.status = "Current"
		second.save(ignore_permissions=True)
		frappe.db.commit()

		first.reload()
		_assert(
			first.status == "Superseded",
			f"The replaced resource is still {first.status}",
		)

		assignments = frappe.get_all(
			"Sparsh Refresher Assignment",
			filters={
				"competency": COMPETENCY,
				"learner": LEARNER,
				"trigger_reason": refresher.RESOURCE_CHANGED,
				"status": "Assigned",
			},
			pluck="name",
		)
		_assert(assignments, "Superseding a learning resource scheduled nobody")
		_assert(
			_state() == "Refresh Due",
			f"After the content changed underneath them the learner is {_state()}",
		)

		# The half that matters most: nothing about the learner's past was rewritten.
		evidence_after = sorted(
			frappe.get_all(
				"Sparsh Evidence",
				filters={"learner": LEARNER, "competency": COMPETENCY},
				fields=evidence_fields,
			),
			key=lambda r: r["name"],
		)
		_assert(
			evidence_after == evidence_before,
			f"Superseding a resource changed the evidence history:\n{evidence_before}\n{evidence_after}",
		)

		certificate.reload()
		_assert(
			certificate.docstatus == 1,
			"Superseding a resource cancelled the learner's certificate instead of suspending it",
		)
		_assert(
			certificate.certification_state == "Suspended",
			f"The certificate stood at {certificate.certification_state} after its content was withdrawn",
		)
	finally:
		competency.reload()
		competency.set("learning_resources", [])
		competency.save(ignore_permissions=True)
		_delete_all("Sparsh Refresher Assignment", {"competency": COMPETENCY})
		_delete_all("Sparsh Certification Record", {"competency": COMPETENCY})
		for name in frappe.get_all(
			"Sparsh Learning Resource", filters={"resource_id": PREFIX + "RES"}, pluck="name"
		):
			frappe.delete_doc(
				"Sparsh Learning Resource", name, force=True, ignore_permissions=True
			)
		frappe.db.commit()


def check_dual_role_cannot_forge_their_own_attempt():
	"""Holding the reviewer role does not let you write your own verdict.

	The chain an independent audit set out: a learner who is also a reviewer takes a
	hint on an activity, then files an Attempt through the ordinary document API
	claiming outcome=Pass at hint level 0. A second reviewer turns that attempt into
	Evidence, and it counts as an unaided pass. The controller skipped its limits for
	anyone unrestricted, and `is_restricted` means "is this a reviewer?" -- the wrong
	question when the subject and the writer are the same person.
	"""
	dual = DUAL_LEARNER
	user = _make_learner(dual)
	if "Sparsh Reviewer" not in [r.role for r in user.roles]:
		user.append("roles", {"role": "Sparsh Reviewer"})
		user.save(ignore_permissions=True)
	_reset_competency()
	frappe.db.commit()

	activity = frappe.get_doc("Sparsh Activity", ACTIVITY_1)
	original_mode, original_expected = activity.evaluation_mode, activity.expected_response
	activity.evaluation_mode = "Deterministic"
	activity.expected_response = "level two"
	activity.save(ignore_permissions=True)
	frappe.db.commit()

	original_user = frappe.session.user
	try:
		# A real hint, earned the ordinary way.
		_submit(ACTIVITY_1, "wrong on purpose", as_user=dual)

		frappe.set_user(dual)
		forged = frappe.new_doc("Sparsh Attempt")
		forged.learner = dual
		forged.activity = ACTIVITY_1
		forged.response = "level two"
		forged.hint_level_used = 0
		forged.outcome = "Pass"
		forged.critical_error = 0
		forged.insert(ignore_permissions=True)

		_assert(
			forged.outcome == "Not Evaluated",
			f"A dual-role user wrote their own verdict: {forged.outcome}",
		)
		_assert(
			forged.hint_level_used >= 1,
			f"A dual-role user claimed hint level {forged.hint_level_used} after taking a hint",
		)
		_assert(
			not forged.independent,
			"A self-filed attempt after a hint was recorded as independent",
		)
	finally:
		frappe.set_user(original_user)
		activity.reload()
		activity.evaluation_mode = original_mode
		activity.expected_response = original_expected
		activity.save(ignore_permissions=True)
		_delete_all("Sparsh Attempt", {"learner": dual})
		frappe.db.commit()


def check_model_ledger_records_cost_and_makes_no_call():
	"""The cost and privacy ledger works, and the engine still makes no model call.

	The programme owner asked for per-interaction provider, model and prompt version,
	tokens, cost, latency, fallback status and a de-identification assertion to be
	queryable *before* a provider is chosen. A gateway added afterwards is a gateway
	somebody routes around.

	The second assertion is the one that must never weaken: no module in this app may
	import a network client or a provider SDK.
	"""
	from sparsh_los import gateway

	_delete_all("Sparsh Model Interaction", {"provider": "zzv-test"})
	frappe.db.commit()

	name = gateway.record(
		provider="zzv-test",
		model_id="zzv-model-1",
		model_version="2026-09",
		purpose="Feedback phrasing",
		learner=LEARNER,
		competency=COMPETENCY,
		prompt_template="zzv-template",
		prompt_version="3",
		input_tokens=1200,
		output_tokens=300,
		estimated_cost=1.5,
		latency_ms=820,
		deidentified=1,
	)
	_assert(name, "The gateway recorded nothing")

	# Re-read what was written. `name` being truthy proves an insert happened, not that
	# any field landed -- a typo'd attribute would be accepted silently by Frappe.
	stored = frappe.get_doc("Sparsh Model Interaction", name)
	for field, expected in (
		("model_version", "2026-09"),
		("prompt_version", "3"),
		("input_tokens", 1200),
		("output_tokens", 300),
		("latency_ms", 820),
		("learner", LEARNER),
		("competency", COMPETENCY),
		("deidentified", 1),
	):
		_assert(
			stored.get(field) == expected,
			f"{field} was recorded as {stored.get(field)!r}, expected {expected!r}",
		)
	_assert(stored.provider == "zzv-test", f"provider was not normalised: {stored.provider}")

	# A second row with a real actual cost, and a third asserting nothing, so the report
	# has all three states to distinguish rather than one trivially-true one.
	gateway.record(
		provider="ZZV-Test", model_id="ZZV-Model-1", purpose="Other", actual_cost=2.0, deidentified=1
	)
	gateway.record(provider="zzv-test", model_id="zzv-model-1", purpose="Other", deidentified=0)
	frappe.db.commit()

	report = gateway.spend(days=1)
	_assert(report["interactions"] >= 3, "The spend report counted fewer than the three fixtures")
	_assert(
		report["total_actual"] >= 2.0,
		f"The billed cost was not reported as actual: {report['total_actual']}",
	)
	_assert(
		report["total_estimated"] >= 1.5,
		f"The estimate was not reported as estimated: {report['total_estimated']}",
	)
	_assert(
		report["cost_is_partly_estimated"],
		"An estimate-only interaction was reported as an actual cost",
	)
	_assert(
		report["interactions_with_no_cost_recorded"] >= 1,
		"An interaction with no cost at all was not reported as unknown",
	)
	_assert(
		report["without_deidentification_assertion"] >= 1,
		"An interaction that asserted nothing was counted as having asserted",
	)

	# A genuine zero is not an estimate. This is the case truthiness discarded.
	zero = gateway.record(
		provider="zzv-test", model_id="zzv-model-1", purpose="Other",
		actual_cost=0.0, estimated_cost=9.0, deidentified=1,
	)
	frappe.db.commit()

	zero_doc = frappe.get_doc("Sparsh Model Interaction", zero)
	_assert(zero_doc.actual_cost == 0.0, "A zero actual cost was not stored")

	# Through spend(), not just off the document. Reading the field back proves the
	# insert; only the report proves cost() honours it -- and with every other
	# assertion a >= lower bound, reverting cost() to truthiness would substitute this
	# row's 9.0 estimate and nothing above would notice.
	after = gateway.spend(days=1)
	_assert(
		after["total_actual"] == report["total_actual"],
		f"A billed zero changed the actual total from {report['total_actual']} "
		f"to {after['total_actual']}; it was treated as an estimate",
	)
	_assert(
		after["total_estimated"] == report["total_estimated"],
		f"A billed zero was added to the estimated total: {after['total_estimated']}",
	)

	# A deliberate estimate of 0.0 is not "no estimate". This is the sibling of the
	# billed-zero case, and it shipped with no fixture at all -- reverting the estimate
	# bucket to truthiness passed every assertion in the harness.
	est_zero = gateway.record(
		provider="zzv-test", model_id="zzv-model-1", purpose="Other",
		estimated_cost=0.0, deidentified=1,
	)
	frappe.db.commit()
	est_doc = frappe.get_doc("Sparsh Model Interaction", est_zero)
	_assert(est_doc.estimated_cost_recorded == 1, "A zero estimate was not recorded as an estimate")
	with_zero_estimate = gateway.spend(days=1)
	_assert(
		with_zero_estimate["interactions_with_no_cost_recorded"]
		== after["interactions_with_no_cost_recorded"],
		"A deliberate estimate of zero was counted as no cost recorded",
	)

	# V2 — the mixed-currency path had no fixture either, and a single non-INR row in
	# the window would have made the assertions above raise TypeError on None.
	gateway.record(
		provider="zzv-test", model_id="zzv-model-1", purpose="Other",
		estimated_cost=5.0, cost_currency="USD", deidentified=1,
	)
	frappe.db.commit()
	mixed = gateway.spend(days=1)
	_assert(mixed["mixed_currency"], "A ledger spanning two currencies was not reported as mixed")
	_assert(mixed["total_cost"] is None, f"A mixed-currency total was still reported: {mixed['total_cost']}")
	for key in ("interactions", "without_deidentification_assertion", "by_model", "errors"):
		_assert(key in mixed, f"The mixed-currency report dropped {key}, which callers read")

	# V3 — reconcile() was new, uncalled and unchecked, and its safety argument rests
	# on track_changes actually being on in the database rather than only in the JSON.
	_assert(
		frappe.get_meta("Sparsh Model Interaction").track_changes,
		"track_changes is off, so an amendment leaves no trail and reconcile() is unsafe",
	)
	_raises(
		lambda: gateway.reconcile(est_zero, actual_cost=None),
		"reconcile accepted no cost at all",
		expect="billed",
	)
	gateway.reconcile(est_zero, actual_cost=3.25, notes="billed in arrears")
	reconciled = frappe.get_doc("Sparsh Model Interaction", est_zero)
	_assert(reconciled.actual_cost == 3.25, f"reconcile did not write the cost: {reconciled.actual_cost}")
	_assert(reconciled.actual_cost_recorded == 1, "reconcile did not mark the cost recorded")
	_assert(
		"billed in arrears" in (reconciled.notes or ""),
		f"reconcile lost the note: {reconciled.notes!r}",
	)

	# Both directions of the de-identification count. Asserting only ">= 1" let a full
	# inversion of the flag pass, which the earlier "== 0" would have caught.
	fixtures = frappe.get_all(
		"Sparsh Model Interaction",
		filters={"provider": "zzv-test"},
		fields=["deidentified"],
	)
	# Count first, so a fixture added later fails with its own cause rather than with a
	# message about de-identification.
	_assert(len(fixtures) == 6, f"{len(fixtures)} fixtures exist, expected 6")
	asserted = len([f for f in fixtures if f.deidentified])
	_assert(asserted == 5, f"{asserted} of the fixtures recorded an assertion, expected 5")
	_assert(
		len(fixtures) - asserted == 1,
		f"{len(fixtures) - asserted} fixtures recorded no assertion, expected 1",
	)

	# And the privacy control runs on what this module can see.
	_raises(
		lambda: gateway.record(
			provider="zzv-test", model_id="zzv-model-1", purpose="Other",
			notes="caregiver WS123456 on 9876543210", deidentified=1,
		),
		"An identifier reached the cost ledger",
		expect="identif",
	)

	# The ledger is a ledger: not writable by hand, even by a System Manager.
	def hand_written():
		doc = frappe.new_doc("Sparsh Model Interaction")
		doc.occurred_at = frappe.utils.now_datetime()
		doc.provider = "zzv-forged"
		doc.model_id = "zzv-forged"
		doc.purpose = "Other"
		doc.deidentified = 1
		doc.insert(ignore_permissions=True)

	_raises(hand_written, "A model interaction could be written by hand", expect="gateway")


	_delete_all("Sparsh Model Interaction", {"provider": "zzv-test"})
	frappe.db.commit()


def _declared_dependencies(path):
	"""Every dependency pyproject declares, across all the places it can declare one.

	Parsed with `tomllib`, not a regex. The hand-rolled version counted a *commented
	out* dependency — the check was green because it read `# "frappe~=16.0.0"` — and
	returned nothing at all for an ordinary extras spec like `celery[redis]>=5`,
	because its non-greedy match stopped at the first `]`. It also could not see
	`[project.optional-dependencies]` or a poetry table. A dependency in any of those
	is a dependency.
	"""
	import tomllib

	data = tomllib.loads(path.read_text(encoding="utf-8"))
	raw = list(data.get("project", {}).get("dependencies", []) or [])
	for group in (data.get("project", {}).get("optional-dependencies", {}) or {}).values():
		raw.extend(group or [])

	poetry = data.get("tool", {}).get("poetry", {})
	for key in ("dependencies", "dev-dependencies"):
		raw.extend((poetry.get(key, {}) or {}).keys())

	import re as _re

	names = []
	for entry in raw:
		# "frappe[all] >= 15" / "frappe @ git+https://..." -> "frappe"
		names.append(_re.split(r"[<>=!~\[@ ;]", str(entry).strip())[0].lower())
	return sorted(n for n in names if n and n != "python")


def check_no_module_imports_a_network_client():
	"""A tripwire for the accident, not a proof of determinism. Say which it is.

	The first version of this banned each module in exactly one of its two spellings --
	`import requests` but not `from requests import post`, `from openai` but not
	`import openai`, which is the form every provider quickstart uses. It would have
	caught almost nothing it was written for.

	It still cannot see a dynamic import, `frappe.get_attr("requests.get")`, or Frappe's
	own post/get request helpers, which need no new import line at all. The control that
	actually holds is `pyproject.toml` declaring no third-party dependency, which is
	why that file is scanned too. This check catches the careless case and nothing more,
	and the docs should not claim otherwise.
	"""
	import pathlib
	import re

	# Split so this file can scan itself: naming the tokens plainly would make the
	# check exempt exactly one file, and exempting by path substring is how the
	# previous version let its own source through.
	modules = (
		"re" + "quests",
		"ht" + "tpx",
		"url" + "lib",
		"soc" + "ket",
		"aio" + "http",
		"anth" + "ropic",
		"op" + "enai",
		"ftp" + "lib",
		"smtp" + "lib",
	)
	pattern = re.compile(
		r"^\s*(?:import|from)\s+(" + "|".join(modules) + r")\b", re.MULTILINE
	)
	# Frappe's own helpers open a socket without any import this could see.
	helpers = ("make_post" + "_request", "make_get" + "_request")

	# The repo root, not the inner package: pyproject.toml is where a dependency would
	# actually arrive, and get_app_path points one level below it.
	root = pathlib.Path(frappe.get_app_path("sparsh_los")).parent
	scanned = 0
	for path in list(root.rglob("*.py")) + list(root.glob("pyproject.toml")):
		if "__pycache__" in str(path):
			continue
		scanned += 1
		text = path.read_text(encoding="utf-8", errors="ignore")
		found = pattern.search(text)
		_assert(
			not found,
			f"{path.name} imports a network client ({found.group(1) if found else ''}); "
			"the engine must make no call",
		)
		for helper in helpers:
			_assert(
				helper not in text,
				f"{path.name} calls frappe.{helper}; the engine must make no call",
			)

	# A floor that means something: the tree has far more than twenty files, so a floor
	# of twenty detected only total collapse of the glob.
	_assert(scanned >= 50, f"The determinism scan only saw {scanned} files; it is not scanning")

	# And the control the docstring calls the real one, actually checked. Applying the
	# import regex to a TOML file -- which the first version did -- can never match
	# `dependencies = ["openai"]`: the file was opened, read, and nothing about it was
	# tested, under a docstring saying it was scanned because it is what holds.
	pyproject = root / "pyproject.toml"
	_assert(pyproject.exists(), "pyproject.toml was not found, so the real control is unchecked")
	declared = _declared_dependencies(pyproject)
	_assert(
		declared == [] or declared == ["frappe"],
		f"pyproject.toml declares third-party dependencies: {declared}. "
		"The engine is stdlib plus frappe, and that list is what actually keeps it deterministic.",
	)


def check_answered_refresher_is_not_reassigned():
	"""The daily job must not re-suspend a learner who has answered their refresher.

	The regression this exists for was introduced by the fix that lets a refresher
	close at all: `evaluate_time_based` read the state while the assignment was still
	open, closed it in the recompute, then assigned a *new* one on that stale reading.
	Nothing could close the new one -- the learner's pass no longer post-dated it --
	so answering a refresher suspended the certificate permanently.
	"""
	from sparsh_los import refresher

	_reset_competency()
	competency = frappe.get_doc("Sparsh Competency", COMPETENCY)
	original_interval = competency.refresh_interval_days
	competency.refresh_interval_days = 1
	competency.save(ignore_permissions=True)
	frappe.db.commit()

	try:
		_new_evidence(ACTIVITY_1, "Pass")
		# Age the demonstration past the interval so the time trigger fires.
		for name in frappe.get_all(
			"Sparsh Evidence", filters={"learner": LEARNER, "competency": COMPETENCY}, pluck="name"
		):
			frappe.db.set_value(
				"Sparsh Evidence", name, "recorded_at", frappe.utils.add_days(frappe.utils.now_datetime(), -30)
			)
		frappe.db.commit()
		recompute = frappe.get_attr("sparsh_los.mastery.recompute_mastery")
		recompute(LEARNER, COMPETENCY)

		refresher.evaluate_time_based()
		_assert(
			_state() == "Refresh Due",
			f"An aged demonstration did not become Refresh Due: {_state()}",
		)

		# The learner answers it.
		_new_evidence(ACTIVITY_2, "Pass")
		_assert(
			_state() in ("Demonstrated", "Mastered"),
			f"Answering the refresher left the learner at {_state()}",
		)

		# And the next daily run must leave them alone.
		refresher.evaluate_time_based()
		_assert(
			_state() in ("Demonstrated", "Mastered"),
			f"The daily job re-suspended a learner who had answered: {_state()}",
		)
		_assert(
			not frappe.db.exists(
				"Sparsh Refresher Assignment",
				{"learner": LEARNER, "competency": COMPETENCY, "status": "Assigned"},
			),
			"The daily job reopened a refresher the learner had already answered",
		)
	finally:
		competency.reload()
		competency.refresh_interval_days = original_interval
		competency.save(ignore_permissions=True)
		_delete_all("Sparsh Refresher Assignment", {"competency": COMPETENCY})
		frappe.db.commit()


def check_draft_rule_cannot_auto_score():
	"""An activity whose rule is not Validated goes to a person, whatever its mode.

	"No unvalidated rule becomes production logic" was a convention in the docs and
	enforced nowhere. An activity set Deterministic against a Draft safety-critical
	rule auto-scored, wrote Evidence and moved Mastery -- the programme owner's single
	hardest constraint, defeated by a field nobody had to change.
	"""
	from sparsh_los import runner

	_reset_competency()
	_delete_all("Sparsh Source of Truth Rule", {"rule_id": PREFIX + "RULE"})
	frappe.db.commit()
	rule = _new_rule(1, status="Draft")
	competency = frappe.get_doc("Sparsh Competency", COMPETENCY)
	competency.set("linked_rules", [])
	competency.append("linked_rules", {"rule": rule.name})
	competency.save(ignore_permissions=True)

	activity = frappe.get_doc("Sparsh Activity", ACTIVITY_1)
	original_mode, original_expected = activity.evaluation_mode, activity.expected_response
	activity.evaluation_mode = "Deterministic"
	activity.expected_response = "level two"
	activity.save(ignore_permissions=True)
	frappe.db.commit()

	try:
		_assert(
			frappe.db.get_value("Sparsh Source of Truth Rule", rule.name, "status") == "Draft",
			"The fixture rule is not Draft; this check proves nothing",
		)
		result = _submit(ACTIVITY_1, "level two")
		_assert(
			result["outcome"] == "Not Evaluated",
			f"A Draft rule auto-scored the learner: {result['outcome']}",
		)
		_assert(
			_state() not in ("Demonstrated", "Mastered"),
			f"A Draft rule moved the learner to {_state()}",
		)

		# Validate the rule and the same submission scores. Without this the check
		# would also pass if the runner had simply stopped scoring anything.
		frappe.db.set_value("Sparsh Source of Truth Rule", rule.name, "status", "Validated")
		frappe.db.commit()
		scored = _submit(ACTIVITY_1, "level two")
		_assert(
			scored["outcome"] == "Pass",
			f"A Validated rule did not score: {scored['outcome']}",
		)

		# And the programme report must say so rather than reporting a pilot is safe.
		frappe.db.set_value("Sparsh Source of Truth Rule", rule.name, "status", "Draft")
		frappe.db.commit()
		from sparsh_los.seed import programme_readiness

		report = programme_readiness()
		_assert(
			ACTIVITY_1 in report["auto_scoring_against_unvalidated_rules"],
			"The readiness report did not name the activity scoring against a Draft rule",
		)
		_assert(
			not report["can_pilot_with_human_review"],
			"The report called a pilot safe while an activity scored against a Draft rule",
		)
	finally:
		activity.reload()
		activity.evaluation_mode = original_mode
		activity.expected_response = original_expected
		activity.save(ignore_permissions=True)
		competency.reload()
		competency.set("linked_rules", [])
		competency.save(ignore_permissions=True)
		frappe.db.commit()


def check_unbuilt_evaluator_modes_fall_to_a_person():
	"""A declared evaluator the engine cannot perform routes to a reviewer, never scores.

	The evaluator types are declared ahead of their implementations so activity content
	can be authored against them. The failure that matters is an unbuilt mode falling
	through to the deterministic string comparison, which would score a rubric or a
	free-text answer as though it were multiple choice.
	"""
	from sparsh_los import runner

	_reset_competency()
	activity = frappe.get_doc("Sparsh Activity", ACTIVITY_1)
	original_mode = activity.evaluation_mode
	original_expected = activity.expected_response
	original_markers = activity.critical_markers

	# Every mode the Select offers except the auto-scored ones, read from the DocType
	# rather than from the constants under test. Iterating the module's own
	# AWAITING_IMPLEMENTATION_MODES meant that moving a mode into AUTO_SCORED_MODES
	# silently removed it from the check -- the check would stop testing exactly the
	# change that most needs testing.
	declared = frappe.get_meta("Sparsh Activity").get_field("evaluation_mode").options.split("\n")
	must_not_score = [m.strip() for m in declared if m.strip() and m.strip() not in runner.AUTO_SCORED_MODES]
	_assert(len(must_not_score) >= 4, f"Only {must_not_score} modes to check; the Select looks wrong")

	try:
		# An exact match against the answer key, so anything that scored at all
		# would score this a Pass.
		activity.expected_response = "level two"
		for mode in must_not_score:
			activity.evaluation_mode = mode
			activity.save(ignore_permissions=True)
			frappe.db.commit()
			outcome, critical = runner.evaluate(
				frappe.get_doc("Sparsh Activity", ACTIVITY_1), "level two"
			)
			_assert(
				outcome == "Not Evaluated",
				f"Mode {mode} scored the response itself: {outcome}",
			)
			_assert(not critical, f"Mode {mode} invented a critical error")

		# Positive control. Without one, a regression that stopped the runner scoring
		# anything at all -- a Draft rule leaking in from the check that runs before
		# this one, say -- would satisfy every assertion in the loop above.
		activity.evaluation_mode = "Deterministic"
		activity.save(ignore_permissions=True)
		frappe.db.commit()
		outcome, _critical = runner.evaluate(
			frappe.get_doc("Sparsh Activity", ACTIVITY_1), "level two"
		)
		_assert(
			outcome == "Pass",
			f"The positive control did not score: {outcome}. The loop above proves nothing.",
		)

		# A critical marker still bites whatever the mode: an unsafe answer is unsafe
		# whether or not the engine can grade the rest of it.
		activity.evaluation_mode = "Rubric"
		activity.critical_markers = "stop the medicine"
		activity.save(ignore_permissions=True)
		frappe.db.commit()
		outcome, critical = runner.evaluate(
			frappe.get_doc("Sparsh Activity", ACTIVITY_1), "I would stop the medicine"
		)
		_assert(
			outcome == "Fail" and critical,
			f"A critical marker was missed under a non-scoring mode: {outcome}/{critical}",
		)
	finally:
		activity.reload()
		activity.evaluation_mode = original_mode
		activity.expected_response = original_expected
		# Restored too: leaving a live critical marker on ACTIVITY_1 armed every check
		# that ran after this one.
		activity.critical_markers = original_markers
		activity.save(ignore_permissions=True)
		frappe.db.commit()


def check_pathway_does_not_hand_over_a_gated_activity():
	"""A mandatory step whose prerequisite is unmet is reported blocked, not offered.

	The pathway walker builds its answer as dict(suggestion, activity=step.activity),
	which forced the step's activity back in even when the suggestion had deliberately
	set it to None with a prerequisite block. The learner page branches on `activity`,
	so a gated activity was rendered with its instruction.
	"""
	from sparsh_los import orchestrator

	_reset_competency()
	_delete_all("Sparsh Pathway", {"name": PATHWAY})

	# A learner of its own. COMPETENCY_2 carries a critical error from earlier checks,
	# and safety outranks the sequence — which would answer "remediation" before the
	# prerequisite is ever read. Critical evidence cannot be deleted by design, so the
	# check takes a clean subject rather than trying to unwind one.
	subject = PREFIX + "pathway@example.invalid"
	_make_learner(subject)
	frappe.db.commit()

	gated = PREFIX + "ACT-GATED"
	if not frappe.db.exists("Sparsh Activity", gated):
		doc = frappe.new_doc("Sparsh Activity")
		doc.activity_id = gated
		doc.title = "Gated activity"
		doc.competency = COMPETENCY_2
		doc.activity_type = "Knowledge check"
		doc.instruction = "Verification instruction."
		doc.version = 1
		doc.evaluation_mode = "Human review"
		doc.insert(ignore_permissions=True)

	# COMPETENCY_2 now requires COMPETENCY, which this learner has not demonstrated.
	competency = frappe.get_doc("Sparsh Competency", COMPETENCY_2)
	competency.set("prerequisites", [])
	competency.append("prerequisites", {"prerequisite": COMPETENCY})
	competency.save(ignore_permissions=True)

	pathway = frappe.new_doc("Sparsh Pathway")
	pathway.pathway_id = PATHWAY
	pathway.title = "Verification pathway"
	pathway.status = "Active"
	pathway.append(
		"steps",
		{"step_order": 1, "activity": gated, "competency": COMPETENCY_2, "is_mandatory": 1},
	)
	pathway.insert(ignore_permissions=True)
	frappe.db.commit()

	try:
		result = orchestrator.next_in_pathway(PATHWAY, subject)
		_assert(
			result.get("reason") == orchestrator.PREREQUISITE,
			f"An unmet prerequisite on a mandatory step gave reason {result.get('reason')}",
		)
		_assert(
			result.get("activity") is None,
			f"A gated activity was handed to the learner anyway: {result.get('activity')}",
		)
		_assert(result.get("blocked_by"), "A prerequisite block named nothing it was blocked by")
	finally:
		competency.reload()
		competency.set("prerequisites", [])
		competency.save(ignore_permissions=True)
		_delete_all("Sparsh Pathway", {"name": PATHWAY})
		frappe.db.commit()


def check_pathway_walks_in_order():
	"""A pathway is walked in order, and safety still outranks the sequence."""
	from sparsh_los import orchestrator

	_reset_competency()
	_delete_all("Sparsh Pathway", {"name": PATHWAY})

	# COMPETENCY_2 needs an activity of its own, or step two has nothing to offer and
	# the pathway correctly reports itself complete.
	step_two_activity = PREFIX + "ACT-P2"
	if not frappe.db.exists("Sparsh Activity", step_two_activity):
		doc = frappe.new_doc("Sparsh Activity")
		doc.activity_id = step_two_activity
		doc.title = "Pathway step two"
		doc.competency = COMPETENCY_2
		doc.activity_type = "Knowledge check"
		doc.instruction = "Verification instruction."
		doc.version = 1
		doc.evaluation_mode = "Human review"
		doc.insert(ignore_permissions=True)
	frappe.db.commit()

	pathway = frappe.new_doc("Sparsh Pathway")
	pathway.pathway_id = PATHWAY
	pathway.title = "Verification pathway"
	pathway.status = "Active"
	pathway.append(
		"steps",
		{"step_order": 1, "activity": ACTIVITY_1, "competency": COMPETENCY, "is_mandatory": 1},
	)
	pathway.append(
		"steps",
		{"step_order": 2, "activity": step_two_activity, "competency": COMPETENCY_2, "is_mandatory": 1},
	)
	pathway.insert(ignore_permissions=True)
	frappe.db.commit()

	first = orchestrator.next_in_pathway(PATHWAY, LEARNER)
	_assert(first["competency"] == COMPETENCY, f"The pathway started at {first.get('competency')}")
	_assert(first.get("step") == 1, "The pathway did not report its step")

	# Demonstrate step one; the pathway moves on.
	_new_evidence(ACTIVITY_1, "Pass")
	second = orchestrator.next_in_pathway(PATHWAY, LEARNER)
	_assert(
		second.get("competency") == COMPETENCY_2,
		f"After demonstrating step one the pathway offered {second.get('competency')}",
	)

	# A critical error on a LATER step must still outrank an incomplete earlier one,
	# or "safety outranks sequence" is only true when safety happens to come first.
	_new_evidence(step_two_activity, "Fail", critical_error=1, competency=COMPETENCY_2)
	back = orchestrator.next_in_pathway(PATHWAY, LEARNER)
	_assert(
		back["reason"] == orchestrator.REMEDIATION and back["competency"] == COMPETENCY_2,
		f"A critical error on a later step was not reached: {back.get('reason')} {back.get('competency')}",
	)
	frappe.db.commit()


def check_nobody_judges_their_own_work():
	"""Role combinations do not create a way to grade yourself."""
	dual = DUAL_LEARNER
	user = _make_learner(dual)
	if "Sparsh Reviewer" not in [r.role for r in user.roles]:
		user.append("roles", {"role": "Sparsh Reviewer"})
		user.save(ignore_permissions=True)
	frappe.db.commit()
	_assert(frappe.db.exists("User", dual), "The dual-role fixture user was not created")

	# Qualify them before the impersonation, and commit, so nothing inside the test
	# can roll the fixture away underneath it.
	_reset_competency()
	_new_evidence(ACTIVITY_1, "Pass", learner=dual)
	_new_evidence(ACTIVITY_2, "Pass", learner=dual)
	# Critical evidence of their own, so the self-review attempt below actually reaches
	# the clearing path. Without it `critical[0]` was never evaluated and the check
	# passed on an unrelated refusal.
	# On a second competency, and on an activity that genuinely belongs to it: a
	# critical error on COMPETENCY would block the self-certification case below, and
	# it would then be refused for safety rather than for judging your own work.
	critical_activity = PREFIX + "ACT-DUAL-C2"
	if not frappe.db.exists("Sparsh Activity", critical_activity):
		act = frappe.new_doc("Sparsh Activity")
		act.activity_id = critical_activity
		act.title = "Dual-role critical activity"
		act.competency = COMPETENCY_2
		act.activity_type = "Knowledge check"
		act.instruction = "Verification instruction."
		act.version = 1
		act.evaluation_mode = "Human review"
		act.insert(ignore_permissions=True)
	own_critical = _new_evidence(
		critical_activity, "Fail", critical_error=1, learner=dual, competency=COMPETENCY_2
	)
	frappe.db.commit()
	_assert(own_critical.name, "The dual-role fixture has no critical evidence to review")

	original_user = frappe.session.user
	try:
		frappe.set_user(dual)

		def self_evidence():
			doc = frappe.new_doc("Sparsh Evidence")
			doc.learner = dual
			doc.competency = COMPETENCY
			doc.activity = ACTIVITY_1
			doc.activity_version = 1
			doc.outcome = "Pass"
			doc.assistance_level = 0
			doc.insert(ignore_permissions=True)

		try:
			self_evidence()
			raise AssertionError("A learner-reviewer wrote evidence about themselves")
		except frappe.PermissionError:
			pass

		def self_certify():
			doc = frappe.new_doc("Sparsh Certification Record")
			doc.learner = dual
			doc.competency = COMPETENCY
			doc.certification_status = "Full"
			doc.insert(ignore_permissions=True)
			doc.submit()

		try:
			self_certify()
			raise AssertionError("A learner-reviewer certified themselves despite qualifying")
		except frappe.PermissionError:
			pass

		def self_review():
			critical = frappe.get_all(
				"Sparsh Evidence",
				filters={"learner": dual, "critical_error": 1, "docstatus": 1},
				pluck="name",
			)
			_assert(critical, "The self-review case has no critical evidence to act on")
			doc = frappe.new_doc("Sparsh Human Review")
			doc.evidence = critical[0]
			doc.clears_critical_error = 1
			doc.learner = dual
			doc.competency = COMPETENCY
			doc.review_status = "Approved"
			doc.insert(ignore_permissions=True)
			doc.submit()

		try:
			self_review()
			raise AssertionError("A learner-reviewer reviewed their own evidence")
		except frappe.PermissionError:
			pass
	finally:
		frappe.set_user(original_user)
		frappe.db.rollback()

	frappe.db.commit()


def check_unenrolled_user_is_shut_out():
	"""A signed-in account with no programme role reaches nothing."""
	from sparsh_los import certification, dashboard, escalation, orchestrator, runner, seed
	from sparsh_los.sparsh_los.doctype.sparsh_certification_record import (
		sparsh_certification_record as cert_record,
	)

	stranger = "zzv-stranger@example.invalid"
	if not frappe.db.exists("User", stranger):
		user = frappe.new_doc("User")
		user.email = stranger
		user.first_name = "Verification"
		user.user_type = "System User"
		user.insert(ignore_permissions=True)
		frappe.db.commit()

	original_user = frappe.session.user
	try:
		frappe.set_user(stranger)
		for call, label in (
			(lambda: dashboard.learner_view(LEARNER), "dashboard.learner_view"),
			(lambda: certification.readiness(COMPETENCY, LEARNER), "certification.readiness"),
			(lambda: orchestrator.next_experience(COMPETENCY, LEARNER), "orchestrator.next_experience"),
			(lambda: dashboard.supervisor_view(), "dashboard.supervisor_view"),
			(lambda: escalation.open_queue(), "escalation.open_queue"),
			(lambda: runner.start(ACTIVITY_1), "runner.start"),
			(lambda: runner.submit(ACTIVITY_1, "anything"), "runner.submit"),
			(lambda: escalation.raise_question("anything"), "escalation.raise_question"),
			(lambda: seed.matrix_status(), "seed.matrix_status"),
			(lambda: seed.case_pack_status(), "seed.case_pack_status"),
			(lambda: certification.certificate_detail("any"), "certification.certificate_detail"),
			(lambda: orchestrator.next_in_pathway(PATHWAY, LEARNER), "orchestrator.next_in_pathway"),
			(lambda: cert_record.current(LEARNER, COMPETENCY), "certification_record.current"),
		):
			try:
				call()
				raise AssertionError(f"{label} was reachable by an unenrolled account")
			except frappe.PermissionError:
				pass
	finally:
		frappe.set_user(original_user)

	frappe.delete_doc("User", stranger, force=True, ignore_permissions=True)
	frappe.db.commit()


def check_mastery_cannot_be_deleted():
	"""A derived state cannot be removed to hide the evidence behind it."""
	_reset_competency()
	_new_evidence(ACTIVITY_1, "Pass")
	name = frappe.db.get_value(
		"Sparsh Mastery State", {"learner": LEARNER, "competency": COMPETENCY}, "name"
	)
	_assert(name, "No mastery state to test deletion against")

	_raises(
		lambda: frappe.delete_doc("Sparsh Mastery State", name, ignore_permissions=True),
		"A derived mastery state was deleted",
	)
	frappe.db.commit()


def check_certification_standing_is_not_editable():
	"""Standing is decided by reconciliation and revocation, never by editing."""
	_reset_competency()
	_new_evidence(ACTIVITY_1, "Pass")

	certificate = frappe.new_doc("Sparsh Certification Record")
	certificate.learner = LEARNER
	certificate.competency = COMPETENCY
	certificate.certification_status = "Full"
	certificate.insert(ignore_permissions=True)
	certificate.submit()
	_assert(certificate.standing_key, "A submitted certificate has no standing key")

	def edit_standing():
		doc = frappe.get_doc("Sparsh Certification Record", certificate.name)
		doc.standing_key = None
		doc.save(ignore_permissions=True)

	_raises(edit_standing, "Certification standing was edited directly")

	# Cancelling frees the slot, so another certification becomes possible.
	certificate.reload()
	certificate.cancel()
	certificate.reload()
	_assert(not certificate.standing_key, "Cancelling left the standing key occupied")

	replacement = frappe.new_doc("Sparsh Certification Record")
	replacement.learner = LEARNER
	replacement.competency = COMPETENCY
	replacement.certification_status = "Full"
	replacement.insert(ignore_permissions=True)
	replacement.submit()
	_assert(replacement.standing_key, "A replacement certificate could not be submitted")
	frappe.db.commit()


def check_refresher_reaches_the_learner():
	"""An assigned refresher is offered, and shown on the learner's own page."""
	from sparsh_los import dashboard, orchestrator, refresher
	from sparsh_los.www import practice

	_reset_competency()
	_delete_all("Sparsh Refresher Assignment", {"competency": COMPETENCY})
	_new_evidence(ACTIVITY_1, "Pass")
	_new_evidence(ACTIVITY_2, "Pass")
	_assert(_state() == "Mastered", f"Expected Mastered, got {_state()}")

	# Mastered with nothing assigned: nothing outstanding.
	quiet = orchestrator.next_experience(COMPETENCY, LEARNER)
	_assert(not quiet.get("activity"), "A mastered competency offered work with no refresher due")

	refresher.assign(LEARNER, COMPETENCY, refresher.RULE_CHANGED, "The rule changed.")
	frappe.db.commit()

	offered = orchestrator.next_experience(COMPETENCY, LEARNER)
	_assert(
		offered["reason"] == orchestrator.REFRESHER,
		f"An assigned refresher was not offered: {offered.get('reason')}",
	)
	_assert(offered.get("activity"), "The refresher offered no activity")

	view = dashboard.learner_view(LEARNER)
	_assert(view["refreshers"], "The learner view does not show the assigned refresher")

	context = frappe._dict()
	original_user = frappe.session.user
	try:
		frappe.set_user(LEARNER)
		practice.get_context(context)
	finally:
		frappe.set_user(original_user)
	_assert(context.view["refreshers"], "The practice page does not show the refresher")
	frappe.db.commit()


def check_certificate_shows_its_working():
	"""A certificate can name the evidence behind it, not merely a verdict."""
	from sparsh_los import certification

	_reset_competency()
	_new_evidence(ACTIVITY_1, "Pass")
	_new_evidence(ACTIVITY_2, "Pass")

	certificate = frappe.new_doc("Sparsh Certification Record")
	certificate.learner = LEARNER
	certificate.competency = COMPETENCY
	certificate.certification_status = "Full"
	certificate.insert(ignore_permissions=True)
	certificate.submit()

	detail = certification.certificate_detail(certificate.name)
	_assert(detail["certified_by"], "The certificate does not name who signed it")
	_assert(detail["certified_on"], "The certificate has no issue date")
	_assert(detail["supporting_evidence"], "The certificate names no supporting evidence")
	_assert(
		len(detail["distinct_activities"]) >= 2,
		f"The certificate lists {detail['distinct_activities']} activities",
	)
	_assert(
		detail["state_now"] in ("Demonstrated", "Mastered"),
		f"The certificate reports the current state as {detail['state_now']}",
	)

	# Another learner cannot read it.
	_make_learner(TEST_LEARNER)
	frappe.db.commit()
	original_user = frappe.session.user
	try:
		frappe.set_user(TEST_LEARNER)
		try:
			certification.certificate_detail(certificate.name)
			raise AssertionError("A learner read somebody else's certificate")
		except frappe.PermissionError:
			pass
	finally:
		frappe.set_user(original_user)
	frappe.db.commit()


def check_supervisor_sees_why_someone_is_stuck():
	"""'Who is stuck' is only useful with 'and why'."""
	from sparsh_los import dashboard

	_reset_competency()
	# Three assisted passes: competent with help, not yet unaided.
	for _ in range(3):
		_new_evidence(ACTIVITY_1, "Pass", assistance_level=2)

	_assert(_state() == "Practising", f"Expected Practising, got {_state()}")

	view = dashboard.supervisor_view(competency=COMPETENCY)
	row = next((r for r in view["stuck"] if r["learner"] == LEARNER), None)
	_assert(row, "A learner with three assisted passes is not shown as stuck")
	_assert(row.get("reason"), "The stuck row gives no reason")
	_assert(
		row["reason"] == "Passing only with help",
		f"The reason was {row['reason']}, expected 'Passing only with help'",
	)
	_assert(row["assisted_passes"] >= 3, "The assisted passes were not counted")
	frappe.db.commit()


def check_programme_summary_counts_from_evidence():
	"""The summary is counted at read time, so it cannot drift from the evidence."""
	from sparsh_los import dashboard

	_reset_competency()
	_new_evidence(ACTIVITY_1, "Pass")
	frappe.db.commit()

	summary = dashboard.programme_summary()
	_assert(summary["certification_ready"] >= 1, "A demonstrated learner is not counted as ready")
	_assert("most_common_gap" in summary, "The summary names no most-common gap")
	_assert(summary["period_days"] == 30, "The default period is not 30 days")

	# A competency short of demonstration shows up as a gap.
	# No activity: the provenance rule applies to unaided passes, not to a partial.
	_new_evidence(None, "Partial", competency=COMPETENCY_2, assistance_level=1)
	frappe.db.commit()
	summary = dashboard.programme_summary()
	_assert(
		COMPETENCY_2 in summary["gaps"],
		f"A competency short of demonstration is not a gap: {summary['gaps']}",
	)

	# It is a supervisor view.
	_make_learner(TEST_LEARNER)
	frappe.db.commit()
	original_user = frappe.session.user
	try:
		frappe.set_user(TEST_LEARNER)
		try:
			dashboard.programme_summary()
			raise AssertionError("A learner read the programme summary")
		except frappe.PermissionError:
			pass
	finally:
		frappe.set_user(original_user)
	frappe.db.commit()


def check_new_learner_can_begin():
	"""Somebody with no evidence at all must have a way to start."""
	from sparsh_los.www import practice

	fresh = "zzv-fresh@example.invalid"
	_make_learner(fresh)
	frappe.db.commit()

	context = frappe._dict()
	original_user = frappe.session.user
	try:
		frappe.set_user(fresh)
		practice.get_context(context)
	finally:
		frappe.set_user(original_user)

	_assert(not context.view["competencies"], "The fresh learner already has competency rows")
	_assert(context.next_up, "A learner with no evidence was offered nothing to begin with")
	_assert(context.next_up.get("activity"), "The offered start has no activity")

	frappe.delete_doc("User", fresh, force=True, ignore_permissions=True)
	frappe.db.commit()


def check_certification_cannot_be_born_suspended():
	"""A crafted certificate cannot start Suspended while holding the standing slot."""
	_reset_competency()
	_new_evidence(ACTIVITY_1, "Pass")

	doc = frappe.new_doc("Sparsh Certification Record")
	doc.learner = LEARNER
	doc.competency = COMPETENCY
	doc.certification_status = "Full"
	doc.certification_state = "Suspended"
	doc.insert(ignore_permissions=True)
	doc.submit()
	doc.reload()
	_assert(
		doc.certification_state == "Active",
		f"A certificate was submitted as {doc.certification_state}",
	)
	frappe.db.commit()


def check_attempt_cannot_be_deleted():
	"""The assistance history cannot be erased."""
	attempt = _new_attempt(None, outcome="Fail", hint_level=2)
	frappe.db.commit()

	previous = frappe.flags.in_sparsh_maintenance
	frappe.flags.in_sparsh_maintenance = False
	try:
		_raises(
			lambda: frappe.delete_doc(
				"Sparsh Attempt", attempt.name, force=True, ignore_permissions=True
			),
			"An attempt was deleted",
		)
	finally:
		frappe.flags.in_sparsh_maintenance = previous
	frappe.db.commit()


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
	("permission_model", check_permission_model),
	("learner_row_scope", check_learner_row_scope),
	("learner_cannot_escape_scope", check_learner_cannot_escape_scope),
	("mastery_requires_distinct_activities", check_mastery_requires_distinct_activities),
	("evidence_activity_must_match_competency", check_evidence_activity_must_match_competency),
	("runner_loop", check_runner_loop),
	("runner_flags_critical_response", check_runner_flags_critical_response),
	("escalation_to_human_review", check_escalation_to_human_review),
	("dashboards", check_dashboards),
	("other_domain_runs_unchanged", check_other_domain_runs_unchanged),
	("orchestrator_selects_next", check_orchestrator_selects_next),
	("only_review_clears_critical_error", check_only_review_clears_critical_error),
	("certification_suspended_on_regression", check_certification_suspended_on_regression),
	("activityless_evidence_cannot_demonstrate", check_activityless_evidence_cannot_demonstrate),
	("identifiers_are_refused", check_identifiers_are_refused),
	("certification_readiness", check_certification_readiness),
	("whitelisted_reads_are_scoped", check_whitelisted_reads_are_scoped),
	("refresher_time_based", check_refresher_time_based),
	("refresher_on_rule_change", check_refresher_on_rule_change),
	("matrix_loads_as_draft", check_matrix_loads_as_draft),
	("clearance_must_be_backed_by_review", check_clearance_must_be_backed_by_review),
	("cancelling_review_restores_block", check_cancelling_review_restores_block),
	("learner_cannot_read_answer_key", check_learner_cannot_read_answer_key),
	("critical_response_reaches_a_person", check_critical_response_reaches_a_person),
	("evidence_cannot_contradict_the_attempt", check_evidence_cannot_contradict_the_attempt),
	("runner_records_the_governing_rule", check_runner_records_the_governing_rule),
	("human_review_activity_completes", check_human_review_activity_completes),
	("review_is_not_open_to_learners", check_review_is_not_open_to_learners),
	("one_standing_certification", check_one_standing_certification),
	("practice_page_builds", check_practice_page_builds),
	("constraints_are_in_the_database", check_constraints_are_in_the_database),
	("rule_refresher_uses_provenance", check_rule_refresher_uses_provenance),
	("review_queue_page", check_review_queue_page),
	("case_pack_loads_for_review_only", check_case_pack_loads_for_review_only),
	("programme_readiness_is_honest", check_programme_readiness_is_honest),
	("activity_cannot_change_competency", check_activity_cannot_change_competency),
	("rejected_evidence_does_not_count", check_rejected_evidence_does_not_count),
	("pathway_walks_in_order", check_pathway_walks_in_order),
	("pathway_does_not_hand_over_a_gated_activity", check_pathway_does_not_hand_over_a_gated_activity),
	("dual_role_cannot_forge_their_own_attempt", check_dual_role_cannot_forge_their_own_attempt),
	("model_ledger_records_cost_and_makes_no_call", check_model_ledger_records_cost_and_makes_no_call),
	("no_module_imports_a_network_client", check_no_module_imports_a_network_client),
	("answered_refresher_is_not_reassigned", check_answered_refresher_is_not_reassigned),
	("draft_rule_cannot_auto_score", check_draft_rule_cannot_auto_score),
	("unbuilt_evaluator_modes_fall_to_a_person", check_unbuilt_evaluator_modes_fall_to_a_person),
	("superseded_resource_does_not_rewrite_history", check_superseded_resource_does_not_rewrite_history),
	("events_are_recorded_and_hold_no_content", check_events_are_recorded_and_hold_no_content),
	("nobody_judges_their_own_work", check_nobody_judges_their_own_work),
	("unenrolled_user_is_shut_out", check_unenrolled_user_is_shut_out),
	("mastery_cannot_be_deleted", check_mastery_cannot_be_deleted),
	("certification_standing_is_not_editable", check_certification_standing_is_not_editable),
	("refresher_reaches_the_learner", check_refresher_reaches_the_learner),
	("certificate_shows_its_working", check_certificate_shows_its_working),
	("supervisor_sees_why_someone_is_stuck", check_supervisor_sees_why_someone_is_stuck),
	("programme_summary_counts_from_evidence", check_programme_summary_counts_from_evidence),
	("new_learner_can_begin", check_new_learner_can_begin),
	("certification_cannot_be_born_suspended", check_certification_cannot_be_born_suspended),
	("attempt_cannot_be_deleted", check_attempt_cannot_be_deleted),
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
