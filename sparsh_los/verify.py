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
OTHER_DOMAIN = PREFIX + "DOM2"
OTHER_COMPETENCY = PREFIX + "COMP3"
OTHER_ACTIVITY = PREFIX + "ACT3"
LEARNER = "Administrator"
DOMAIN = PREFIX + "DOM"
COMPETENCY = PREFIX + "COMP"
COMPETENCY_2 = PREFIX + "COMP2"
ACTIVITY_1 = PREFIX + "ACT1"
ACTIVITY_2 = PREFIX + "ACT2"
RULE_ID = PREFIX + "RULE"
TEST_LEARNER = "zzv-learner@example.invalid"
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
	_delete_all("Sparsh Mastery State", {"competency": competency})
	_delete_all("Sparsh Attempt", {"activity": ("in", [ACTIVITY_1, ACTIVITY_2])})
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
	_delete_all("Sparsh Mastery State", {"competency": ("in", competencies)})
	_delete_all("Sparsh Escalation Question", {"learner": ("in", [LEARNER, TEST_LEARNER, OTHER_LEARNER])})
	_delete_all("Sparsh Attempt", {"activity": ("in", [ACTIVITY_1, ACTIVITY_2, OTHER_ACTIVITY])})
	_delete_all("Sparsh Activity", {"name": ("in", [ACTIVITY_1, ACTIVITY_2, OTHER_ACTIVITY])})
	_delete_all("Sparsh Competency", {"name": ("in", competencies)})
	_delete_all("Sparsh Competency Domain", {"name": ("in", [DOMAIN, OTHER_DOMAIN])})
	_delete_all("Sparsh Source of Truth Rule", {"rule_id": RULE_ID})
	for user in (TEST_LEARNER, OTHER_LEARNER):
		if frappe.db.exists("User", user):
			frappe.delete_doc("User", user, force=True, ignore_permissions=True)
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
		activity.hints = "First hint.\nSecond hint."
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

	wrong = runner.submit(ACTIVITY_1, "level four")
	_assert(wrong["outcome"] == "Fail", "A wrong answer was not marked Fail")
	_assert(wrong["hint_level"] == 1, "The hint ladder did not advance")
	_assert(wrong["hint"], "No hint was returned after a wrong answer")
	_assert("evidence" not in wrong, "A failed attempt produced evidence")
	_assert(_state() != "Demonstrated", "A failed attempt moved the mastery state")

	# A pass after a hint is practice evidence, not demonstration. The distinction is
	# the point of the hint ladder: help received is part of the record.
	right = runner.submit(ACTIVITY_1, "  Level Two  ")
	_assert(right["outcome"] == "Pass", "A correct answer was not marked Pass")
	_assert(right.get("evidence"), "A pass produced no evidence")
	_assert(
		_state() == "Practising",
		f"An assisted pass gave {_state()}, expected Practising",
	)

	assistance = frappe.db.get_value("Sparsh Evidence", right["evidence"], "assistance_level")
	_assert(assistance == 1, f"Assistance level recorded as {assistance}, expected 1")

	# An unaided pass is what moves the learner to Demonstrated. A fresh activity is
	# needed: assistance is now counted from the record, and this one has a failure.
	independent = runner.submit(ACTIVITY_2, "level two")
	_assert(independent["outcome"] == "Pass", "An unaided correct answer was not marked Pass")
	_assert(
		_state() == "Demonstrated",
		f"An independent pass gave {_state()}, expected Demonstrated",
	)
	frappe.db.commit()


def check_runner_flags_critical_response():
	"""A response matching a declared critical error fails and blocks progression."""
	from sparsh_los import runner

	result = runner.submit(ACTIVITY_1, "I would tell them to stop the medicine")
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

	right = runner.submit(OTHER_ACTIVITY, "Share")
	_assert(right["outcome"] == "Pass", "The other-domain activity did not evaluate a correct answer")
	_assert(right.get("evidence"), "The other-domain pass produced no evidence")

	wrong = runner.submit(OTHER_ACTIVITY, "file")
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

	_new_evidence(None, "Pass")
	_new_evidence(None, "Pass")
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

	_raises(forge_clearance, "A clearance without a review was accepted")

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
		_assert(not doc.get("critical_errors"), "A learner could read the critical-error markers")

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

	result = runner.submit(ACTIVITY_1, "I would tell them to stop the medicine")
	_assert(result["critical_error"] == 1, "A critical response was not flagged")
	_assert(result.get("escalation"), "A critical response raised no escalation")

	question = frappe.get_doc("Sparsh Escalation Question", result["escalation"])
	_assert(question.status == "Open", "The automatic escalation is not open")
	_assert(question.escalation_reason == "Safety critical", "The escalation reason is wrong")

	# A directly negated mention is not unsafe.
	negated = runner.submit(ACTIVITY_1, "I would tell them to not stop the medicine")
	_assert(
		not negated["critical_error"],
		"A negated mention of a critical marker was flagged as unsafe",
	)

	# Punctuation does not smuggle an unsafe answer past the marker.
	punctuated = runner.submit(ACTIVITY_1, "Simple: stop the medicine.")
	_assert(punctuated["critical_error"] == 1, "Punctuation defeated the critical marker")

	# And a distant negation is not a negation of this phrase.
	distant = runner.submit(ACTIVITY_1, "Do not hesitate to stop the medicine")
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

	result = runner.submit(ACTIVITY_1, "level two")
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
	practice.get_context(context)

	_assert(context.learner == frappe.session.user, "The page named the wrong learner")
	_assert(context.view["competencies"], "The page shows no competencies")
	_assert(
		any(c["competency"] == COMPETENCY for c in context.view["competencies"]),
		"The page is missing the competency just demonstrated",
	)

	# The page must offer the next thing to do, with an instruction to show.
	if context.next_up:
		_assert(context.next_up.get("activity"), "next_up carries no activity")
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

	raised = False
	try:
		duplicate_evidence()
	except Exception:
		raised = True
		frappe.db.rollback()
	_assert(raised, "The database accepted two evidence rows for one attempt")
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
