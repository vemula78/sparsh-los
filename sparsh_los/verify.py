# Copyright (c) 2026, SSSIHMS and contributors
# For license information, please see license.txt

"""Behavioural acceptance check for the engine.

Run with:  bench --site <site> execute sparsh_los.verify.run

Creates its own fixtures under the ZZV- prefix, exercises the eight named checks,
deletes everything it created, and exits non-zero on any failure. Re-runnable:
leftovers from an interrupted run are cleared before the first check.
No patient data, no real learner data, no network access.
"""

import pathlib
import re
import sys
import traceback

import frappe
import frappe.client

from sparsh_los.mastery import DEMONSTRATED, MASTERED, REJECTED, STATE_ORDER, derive_state

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
BASE_RULE_ID = PREFIX + "BASERULE"
TEST_LEARNER = "zzv-learner@example.invalid"
DUAL_LEARNER = "zzv-dual@example.invalid"
OTHER_LEARNER = "zzv-other@example.invalid"
SUMMARY_LEARNER = "zzv-summary@example.invalid"

PII_PATTERN = re.compile(r"patient|mrn|uhid|dob|aadhaar|phone|address", re.IGNORECASE)
# `sai` needs a word boundary. As a bare substring it matches ordinary English --
# "said" was the first false positive, on a field description -- and a tripwire that
# fires on innocent prose is one somebody eventually weakens to make a build pass,
# which is worse than not having it. The boundary still catches "SAI SPARSH",
# "SAI-SPARSH" and a bare "SAI"; "saisparsh" is caught by the other alternative.
DOMAIN_STRING_PATTERN = re.compile(r"sparsh|\bsai\b", re.IGNORECASE)

results = []
# Checks that could not run here, with the reason. Never folded into the pass count.
skipped = []


class Inapplicable(Exception):
	"""This check cannot be run here, and saying so is not the same as passing.

	Two checks read a whole-window figure and assert its exact value, which is only
	possible when every attempt in the window is the harness's own. On a site carrying
	the demonstration cohort that is false, and the honest options are to skip loudly or
	to report a number that has been quietly contaminated. A skip is counted and named
	separately from a pass so that coverage lost this way is visible in the output
	rather than inferred from a total that looks healthy.
	"""


def _check(name, fn):
	try:
		fn()
		results.append((name, True, ""))
		print(f"PASS {name}")
	except Inapplicable as exc:
		frappe.db.rollback()
		skipped.append((name, str(exc).strip()))
		print(f"SKIP {name}: {exc}")
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
	it tests and still pass on an unrelated refusal further down. Every call site now
	passes one: the reasons were collected by logging what each refusal actually said,
	rather than guessed from the code the check is meant to be independent of.
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



def _refused(fn, message, expect=None):
	"""Assert fn is refused by the permission layer specifically.

	`_raises` admits only `frappe.ValidationError`, and `frappe.PermissionError` is not
	one of its subclasses on this version -- so a permission refusal reached `_raises`
	as an unexpected exception and failed the check it was meant to satisfy. Splitting
	them keeps both precise: a validation refusal here would be the wrong layer and is
	reported as such.
	"""
	frappe.db.savepoint("sparsh_refused")
	try:
		fn()
	except frappe.PermissionError as exc:
		frappe.db.rollback(save_point="sparsh_refused")
		if expect and expect.lower() not in str(exc).lower():
			raise AssertionError(f"{message} (refused, but for another reason: {exc})") from None
		return
	except Exception as exc:  # noqa: BLE001
		frappe.db.rollback(save_point="sparsh_refused")
		raise AssertionError(
			f"{message} (refused by another layer: {type(exc).__name__}: {exc})"
		) from None
	frappe.db.rollback(save_point="sparsh_refused")
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
		# `delete_doc` enqueues `delete_dynamic_links` once per row. This bench runs no
		# worker, so a full run's own cleanup queued ~800 jobs and the framework refused
		# the 701st with QueueOverloaded -- in teardown, after every check had passed.
		# `frappe.in_test` is the framework's own switch for running that job inline;
		# it is set for the delete alone and restored, not cleared.
		previous_in_test = frappe.in_test
		frappe.in_test = True
		try:
			frappe.delete_doc(doctype, name, force=True, ignore_permissions=True)
		finally:
			frappe.in_test = previous_in_test


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
	_delete_all("Sparsh Source of Truth Rule", {"rule_id": BASE_RULE_ID})
	frappe.db.commit()

	# ...and put a permitting one back. The comment above says a leftover Draft rule
	# stops the runner and reads as a regression in the next check; now that the gate
	# fails closed, *no* rule stops it in exactly the same way. Restoring the ordinary
	# case -- a Validated rule that permits automation -- keeps reset meaning "back to
	# normal" rather than "back to blocked". Checks about the gate unlink it themselves.
	if frappe.db.exists("Sparsh Competency", competency):
		_link_permitting_rule(competency)


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
	# Cohorts point at the pathway and at fixture users; they go before both.
	_delete_all("Sparsh Cohort", {"name": ("like", PREFIX + "%")})
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
		SUMMARY_LEARNER,
		"zzv-fresh@example.invalid",
		"zzv-stranger@example.invalid",
		PREFIX + "pathway@example.invalid",
		"zzv-reviewer@example.invalid",
		"zzv-inactive@example.invalid",
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


def _new_rule(version, supersedes=None, status="Validated", automation_status="Safe as fixed logic"):
	"""A fixture rule.

	`automation_status` defaults to one that permits automation because most checks
	here exercise scoring and need a rule that allows it. It is explicit rather than
	blank: the gate now refuses to automate a rule that does not say it may be, so a
	blank fixture would silently stop the very behaviour a scoring check is testing,
	and the failure would read as a scoring bug rather than a missing fixture field.
	"""
	rule = frappe.new_doc("Sparsh Source of Truth Rule")
	rule.rule_id = RULE_ID
	rule.version = version
	rule.rule_statement = "Verification rule statement."
	rule.status = status
	rule.automation_status = automation_status
	if status == "Validated":
		# Validated requires the four things that make it a claim about a person: the
		# approved wording, who approved it, the document that says so, and the date.
		# The fixture supplies them so the checks exercise the engine rather than the
		# guard -- and so a fixture can never accidentally create the state the guard
		# exists to prevent. Note it does NOT set `rule_owner`: an approver need not
		# hold an account here, and requiring one blocked the first real decision.
		rule.approved_statement = "Verification approved wording."
		rule.approved_by_name = "Verification Programme Owner"
		rule.approval_source = "Verification governance record, undated fixture."
		rule.effective_date = frappe.utils.today()
	rule.supersedes = supersedes
	rule.insert(ignore_permissions=True)
	return rule


def _link_permitting_rule(competency=None):
	"""Give a fixture competency a Validated rule that permits automation.

	The rule gate now fails closed: a competency with no rule linked is not scored by
	the engine at all, and a Validated rule that does not say it may be automated is
	not either. That is the point of the gate, but almost every scoring check here was
	written when an unruled competency scored freely, so without this their fixtures
	would exercise the gate instead of the behaviour they name.

	Checks that are *about* the gate remove the link themselves.
	"""
	competency = competency or COMPETENCY
	existing = frappe.get_all(
		"Sparsh Competency Rule Link", filters={"parent": competency}, pluck="rule"
	)
	if existing:
		return existing[0]

	# Its own rule_id, not the shared fixture one: several checks create
	# `PREFIX + "RULE"` versions themselves, and a baseline sharing that name collides
	# on insert and fails the check for a reason that has nothing to do with it.
	name = BASE_RULE_ID + "-v1"
	if not frappe.db.exists("Sparsh Source of Truth Rule", name):
		rule = frappe.new_doc("Sparsh Source of Truth Rule")
		rule.rule_id = BASE_RULE_ID
		rule.version = 1
		rule.rule_statement = "Baseline fixture rule: the ordinary, automatable case."
		rule.status = "Validated"
		rule.automation_status = "Safe as fixed logic"
		rule.approved_statement = "Baseline approved wording."
		rule.approved_by_name = "Verification Programme Owner"
		rule.approval_source = "Verification governance record, undated fixture."
		rule.effective_date = frappe.utils.today()
		rule.insert(ignore_permissions=True)
	rule = frappe._dict({"name": name})
	doc = frappe.get_doc("Sparsh Competency", competency)
	doc.append("linked_rules", {"rule": rule.name})
	doc.save(ignore_permissions=True)
	frappe.db.commit()
	return rule.name


def _unlink_rules(competency=None):
	"""Take every rule off a fixture competency, for checks about the unruled case."""
	competency = competency or COMPETENCY
	doc = frappe.get_doc("Sparsh Competency", competency)
	if doc.get("linked_rules"):
		doc.set("linked_rules", [])
		doc.save(ignore_permissions=True)
	frappe.db.commit()


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

	_raises(mutate, "Editing a stored rule_version was allowed", expect="historical snapshot")
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

	_raises(launder, "Evidence citing a critical attempt was allowed to record a pass", expect="contradicts the attempt")
	frappe.db.commit()


def check_mastery_not_directly_settable():
	def force_state():
		doc = frappe.new_doc("Sparsh Mastery State")
		doc.learner = LEARNER
		doc.competency = COMPETENCY_2
		doc.state = "Mastered"
		doc.insert(ignore_permissions=True)

	_assert(not frappe.flags.in_mastery_recompute, "recompute flag leaked from an earlier step")
	_raises(force_state, "A Mastery State was insertable directly", expect="cannot be set directly")


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

	_raises(certify, "Certification was allowed despite a standing critical error", expect="blocks certification")
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
		# The DocType name itself, and every fieldname. The convention forbids a
		# `sparsh_`-prefixed fieldname, and the scan that enforced it read only three
		# display attributes -- so the one thing named in the rule went unchecked.
		bare = doctype.replace("Sparsh ", "")
		if DOMAIN_STRING_PATTERN.search(bare):
			offenders.append(f"doctype:{doctype}")
		for field in frappe.get_meta(doctype).fields:
			if DOMAIN_STRING_PATTERN.search(field.fieldname or ""):
				offenders.append(f"{doctype}.{field.fieldname} (fieldname)")
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

	# All six scoped DocTypes, not just Attempt. A missing hook or a wrong learner
	# field on any of the others would have left every row of it readable, and this
	# check -- named for row scope in general -- would still have passed.
	from sparsh_los import hooks
	from sparsh_los.permissions import LEARNER_SCOPED

	for doctype, field in LEARNER_SCOPED.items():
		_assert(
			doctype in hooks.permission_query_conditions,
			f"{doctype} is learner-scoped but has no permission_query_conditions hook",
		)
		_assert(
			doctype in hooks.has_permission,
			f"{doctype} is learner-scoped but has no has_permission hook",
		)
		_assert(
			frappe.get_meta(doctype).get_field(field),
			f"{doctype} is scoped on {field}, which is not a field on it",
		)
		scoped = frappe.get_attr(hooks.permission_query_conditions[doctype])(TEST_LEARNER)
		_assert(
			TEST_LEARNER in (scoped or ""),
			f"{doctype}'s condition does not name the learner: {scoped!r}",
		)
		_assert(
			frappe.get_attr(hooks.permission_query_conditions[doctype])("Administrator") == "",
			f"{doctype} scoped Administrator like a learner",
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

	_raises(cross_credit, "Evidence credited a competency the activity does not belong to", expect="belongs to competency")
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

	_raises(bad_disposition, "An unrecognised disposition was accepted", expect="not a recognised disposition")

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
	pending_before = view["pending_escalations"]
	# Naming the fixture, not counting heads: `learners >= 1` is true on this site
	# whether or not this check's own learner reached the view.
	_assert(
		LEARNER in {r["learner"] for r in view["ready_to_progress"]} | {r["learner"] for r in view["stuck"]},
		f"The verification learner is absent from the supervisor view: {view['learners']} learners seen",
	)
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

	# The other three answers, each on a named fixture. `critical_errors`,
	# `pending_escalations` and `programme_signals` were returned and never read.
	_assert(
		not any(r["learner"] == LEARNER and r["activity"] == ACTIVITY_2 for r in view["critical_errors"]),
		"The fixture already appears among the critical errors, so nothing below discriminates",
	)
	from sparsh_los import escalation

	_make_learner(TEST_LEARNER)
	frappe.db.commit()
	original_user = frappe.session.user
	try:
		frappe.set_user(TEST_LEARNER)
		question = escalation.raise_question(
			"Which section of the manual covers this?", activity=ACTIVITY_1, reason="Unknown"
		)
	finally:
		frappe.set_user(original_user)
	_new_evidence(ACTIVITY_2, "Fail", critical_error=1)
	frappe.db.commit()

	view = dashboard.supervisor_view(competency=COMPETENCY)
	_assert(
		any(r["learner"] == LEARNER and r["activity"] == ACTIVITY_2 for r in view["critical_errors"]),
		"A critical error on the fixture learner is absent from critical_errors",
	)
	_assert(
		view["pending_escalations"] - pending_before == 1,
		f"An open question did not move pending_escalations: {pending_before} -> {view['pending_escalations']}",
	)
	_assert(
		not any(q["name"] == question for q in view["programme_signals"]),
		"An unanswered question is already a programme signal",
	)

	escalation.answer(question, "Section three. The material needs a worked example.", "Curriculum change")
	frappe.db.commit()
	view = dashboard.supervisor_view(competency=COMPETENCY)
	signal = next((q for q in view["programme_signals"] if q["name"] == question), None)
	_assert(signal, "A question answered with a programme-level disposition is not a programme signal")
	_assert(signal["disposition"] == "Curriculum change", f"The signal carries {signal['disposition']!r}")
	_assert(
		view["pending_escalations"] == pending_before,
		f"An answered question still counts as pending: {view['pending_escalations']} vs {pending_before}",
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

	# A governing rule that permits automation. This check is about the engine being
	# domain-agnostic, not about the rule gate: without a rule the gate now -- rightly
	# -- routes the answer to a person, and the check would fail for a reason that has
	# nothing to do with the domain. A digital-skills competency has a governing rule
	# like any other; it simply is not a clinical one.
	_link_permitting_rule(OTHER_COMPETENCY)

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
	_assert(
		LEARNER in {r["learner"] for r in view["ready_to_progress"]} | {r["learner"] for r in view["stuck"]},
		"The other-domain learner is invisible to the supervisor view",
	)
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

	_raises(certify, "Certification was allowed while a critical error stood", expect="blocks certification")

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
		expect="carries no provenance",
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

	_raises(mrn_in_response, "An MRN-shaped identifier was accepted in an attempt", expect="patient identifier")

	def aadhaar_in_question():
		escalation.raise_question("Caregiver quoted 123456789012, what do I do?", activity=ACTIVITY_1)

	_raises(aadhaar_in_question, "A 12-digit identifier was accepted in an escalation", expect="patient identifier")

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

		_raises(attempt_with, f"{label} was accepted", expect="patient identifier")

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
	_assert(
		any(r["learner"] == LEARNER for r in cohort["buckets"][certification.BLOCKED]),
		f"The cohort view did not report this learner as blocked: "
		f"{cohort['buckets'][certification.BLOCKED]}",
	)
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

	# `evaluate_time_based` returns every assignment it made site-wide, so a truthy
	# result says only that somebody, somewhere, was assigned. The fixture is named.
	def fixture_assignments(names):
		return [
			n
			for n in names
			if frappe.db.get_value(
				"Sparsh Refresher Assignment", n, ["learner", "competency"], as_dict=True
			) == {"learner": LEARNER, "competency": COMPETENCY}
		]

	assigned = refresher.evaluate_time_based()
	ours = fixture_assignments(assigned)
	_assert(
		len(ours) == 1,
		f"An aged demonstration did not assign the fixture learner exactly one refresher: "
		f"{len(ours)} of {len(assigned)} assignment(s) were theirs",
	)
	_assert(
		frappe.db.get_value("Sparsh Refresher Assignment", ours[0], "trigger_reason")
		== refresher.TIME_ELAPSED,
		"The refresher does not say it was time that triggered it",
	)

	# Idempotent: a second run does not pile up duplicates for this learner.
	again = refresher.evaluate_time_based()
	_assert(
		not fixture_assignments(again),
		f"A second run duplicated the fixture learner's refresher: {fixture_assignments(again)}",
	)

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
	"""The loader creates rules as Draft and leaves existing ones alone.

	Asserted on what the loader *did*, not on the seed data's current status: the first
	version asserted `Validated == 0` across the matrix, which goes red on any site where
	the programme owner has validated one rule -- the state the platform exists to reach.
	The status classification is then exercised by moving one matrix rule through the
	states and back, so it is the engine's reading that is tested and not the seed's.
	"""
	from sparsh_los import seed

	matrix_ids = [row["rule_id"] for row in seed._rows()]
	# Not a fixed count. The matrix is the programme owner's document and he adds rows to
	# it -- an eighteenth arrived with his first set of decisions. A hard 17 would have
	# gone red on the day he answered, which is the day it matters most that the harness
	# still runs. What must hold is that every row he wrote produced a rule.
	_assert(matrix_ids, "The matrix source holds no rules at all")
	_assert(
		len(matrix_ids) == len(set(matrix_ids)),
		f"The matrix source repeats a rule id: {sorted(matrix_ids)}",
	)

	before = {
		r.name: r.status
		for r in frappe.get_all(
			"Sparsh Source of Truth Rule", filters={"rule_id": ("in", matrix_ids)}, fields=["name", "status"]
		)
	}
	created, skipped = seed.load_matrix()
	_assert(
		sorted(created and [frappe.db.get_value("Sparsh Source of Truth Rule", n, "rule_id") for n in created] or [])
		+ sorted(skipped) == sorted(matrix_ids),
		f"The loader did not account for every matrix rule: created={created} skipped={skipped}",
	)
	for name in created:
		_assert(
			frappe.db.get_value("Sparsh Source of Truth Rule", name, "status") == "Draft",
			f"The loader created {name} as something other than Draft",
		)
	for name, status in before.items():
		_assert(
			frappe.db.get_value("Sparsh Source of Truth Rule", name, "status") == status,
			f"The loader changed the status of an existing rule {name}",
		)

	# Re-running must not duplicate.
	count_before = frappe.db.count("Sparsh Source of Truth Rule")
	again_created, _ = seed.load_matrix()
	_assert(not again_created, f"Re-running the matrix loader created rules: {again_created}")
	_assert(
		frappe.db.count("Sparsh Source of Truth Rule") == count_before,
		"Re-running the matrix loader duplicated rules",
	)

	# The classification, on one safety-critical matrix rule moved through the states.
	probe = frappe.get_all(
		"Sparsh Source of Truth Rule",
		filters={"rule_id": ("in", matrix_ids), "criticality": "Safety-critical", "status": ("!=", "Superseded")},
		fields=["name", "rule_id", "status", "automation_status"],
		order_by="rule_id asc, version desc",
		limit=1,
	)
	_assert(probe, "The matrix holds no safety-critical rule to classify")
	probe = probe[0]
	try:
		frappe.db.set_value("Sparsh Source of Truth Rule", probe.name, "status", "Draft", update_modified=False)
		status = seed.matrix_status()
		_assert(
			probe.rule_id in status["unvalidated_safety_critical"],
			f"A Draft safety-critical rule is not reported as awaiting validation: {status['unvalidated_safety_critical']}",
		)
		_assert(
			probe.rule_id not in status["ready_to_automate"],
			"A Draft rule is reported ready to automate",
		)
		_assert(status["by_status"].get("Draft", 0) >= 1, "A Draft rule is missing from the status tally")

		frappe.db.set_value("Sparsh Source of Truth Rule", probe.name, "status", "Validated", update_modified=False)
		status = seed.matrix_status()
		_assert(
			probe.rule_id not in status["unvalidated_safety_critical"],
			"A Validated rule is still reported as awaiting validation",
		)
		_assert(
			(probe.rule_id in status["ready_to_automate"])
			== (probe.automation_status == "Safe as fixed logic"),
			f"Ready-to-automate disagrees with the rule's automation status "
			f"({probe.automation_status!r}): {status['ready_to_automate']}",
		)
	finally:
		frappe.db.set_value("Sparsh Source of Truth Rule", probe.name, "status", probe.status, update_modified=False)
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
		expect="cannot be cancelled",
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

		# Field stripping is the second line, not the guarantee. The stated invariant is
		# that a learner cannot read Activity at all, and testing only the stripping
		# would pass a regression that granted permlevel-0 document access while keeping
		# the answer fields at permlevel 1.
		_refused(
			lambda: frappe.get_doc("Sparsh Activity", ACTIVITY_1).check_permission("read"),
			"A learner could read the Activity document",
		)
		_refused(
			lambda: frappe.client.get_list(
				"Sparsh Activity", filters={"name": ACTIVITY_1}, fields=["name"], limit_page_length=1
			),
			"A learner could list Activity rows",
			expect="insufficient permission",
		)
		# A filter on a hidden field is a prefix oracle even when the field is stripped
		# from the result, so the list path has to be closed, not just the field.
		_refused(
			lambda: frappe.client.get_list(
				"Sparsh Activity",
				filters={"expected_response": ("like", "level%")},
				fields=["name"],
				limit_page_length=1,
			),
			"A learner could filter Activity on the answer key",
			expect="insufficient permission",
		)

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

	_raises(upgrade, "Evidence upgraded a failed attempt to a pass", expect="contradicts the attempt")

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

	_raises(understate_help, "Evidence claimed less assistance than the attempt recorded", expect="less assistance")

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
		expect="already been turned into evidence",
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

	_raises(duplicate, "Two certifications stand for the same competency", expect="already stands for this competency")

	# A revocation must name what it withdraws.
	def unattached_revocation():
		bad = frappe.new_doc("Sparsh Certification Record")
		bad.learner = LEARNER
		bad.competency = COMPETENCY
		bad.certification_status = "Revoked"
		bad.insert(ignore_permissions=True)

	_raises(unattached_revocation, "A revocation naming no certification was accepted", expect="must name the certification")

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
	"""The loader creates cases in Human review, and the status report reads the mode.

	Asserted on what the loader created and on a delta, not on the seed's current state:
	the first version required every loaded case to be awaiting review, which goes red
	the day the programme owner validates a rule and gives one case an answer.
	"""
	from sparsh_los import seed

	created, skipped = seed.load_case_pack()
	for name in created:
		row = frappe.db.get_value(
			"Sparsh Activity", name, ["evaluation_mode", "expected_response"], as_dict=True
		)
		_assert(
			row.evaluation_mode == "Human review" and not row.expected_response,
			f"The loader created {name} able to score itself: {row}",
		)

	# The competencies the cases belong to exist.
	for competency_id in ("SSP-RISK", "SSP-PLEDGE", "SSP-SCOPE"):
		_assert(
			frappe.db.exists("Sparsh Competency", competency_id),
			f"Competency {competency_id} was not created",
		)

	# Re-running does not duplicate.
	before = frappe.db.count("Sparsh Activity", {"activity_id": ("like", "SC-%")})
	again_created, _ = seed.load_case_pack()
	_assert(not again_created, f"Re-running the case pack loader created activities: {again_created}")
	_assert(
		frappe.db.count("Sparsh Activity", {"activity_id": ("like", "SC-%")}) == before,
		"Re-running the case pack loader duplicated activities",
	)

	# The report reads the mode: one case moved to Deterministic and back must appear
	# in `auto_scored` and leave `awaiting_human_review`, whatever the rest are set to.
	probe = frappe.get_all(
		"Sparsh Activity",
		filters={"activity_id": ("like", "SC-%"), "evaluation_mode": "Human review"},
		fields=["name"],
		order_by="name asc",
		limit=1,
	)
	_assert(probe, "No case is in Human review, so the report's reading cannot be tested")
	probe = probe[0].name
	base = seed.case_pack_status()
	_assert(probe not in base["auto_scored"], "A Human-review case is reported as auto-scored")
	try:
		frappe.db.set_value("Sparsh Activity", probe, "evaluation_mode", "Deterministic", update_modified=False)
		flipped = seed.case_pack_status()
		_assert(
			probe in flipped["auto_scored"],
			f"A Deterministic case is not reported as auto-scored: {flipped['auto_scored']}",
		)
		_assert(
			flipped["awaiting_human_review"] == base["awaiting_human_review"] - 1,
			f"Moving one case out of Human review changed awaiting_human_review "
			f"{base['awaiting_human_review']} -> {flipped['awaiting_human_review']}",
		)
		_assert(flipped["loaded"] == base["loaded"], "Changing a mode changed the loaded count")
	finally:
		frappe.db.set_value("Sparsh Activity", probe, "evaluation_mode", "Human review", update_modified=False)
		frappe.db.commit()

def check_programme_readiness_is_honest():
	"""The readiness report tells the programme owner the truth.

	Built on the harness's own rule and activity, so it reads the engine's judgement
	and not the seed's current position: the first version asserted that nothing in the
	matrix was ready to automate, which is exactly the assertion that goes red on a
	pilot-configured site.
	"""
	from sparsh_los import seed

	_reset_competency()
	draft = _new_rule(1, status="Draft")
	competency = frappe.get_doc("Sparsh Competency", COMPETENCY)
	competency.append("linked_rules", {"rule": draft.name})
	competency.save(ignore_permissions=True)

	activity = frappe.get_doc("Sparsh Activity", ACTIVITY_1)
	original_mode, original_expected = activity.evaluation_mode, activity.expected_response
	activity.evaluation_mode = "Deterministic"
	activity.expected_response = "level two"
	activity.save(ignore_permissions=True)
	frappe.db.commit()

	try:
		report = seed.programme_readiness()
		_assert(
			ACTIVITY_1 in report["auto_scoring_against_unvalidated_rules"],
			f"A Deterministic activity under a Draft rule is not reported: "
			f"{report['auto_scoring_against_unvalidated_rules']}",
		)
		_assert(
			report["can_pilot_with_human_review"] is False,
			"The report called the pilot human-review-safe while an activity would score "
			"itself against a Draft rule",
		)
		_assert(
			COMPETENCY not in report["competencies_without_rules"],
			"A competency with a rule linked is reported as having none",
		)
		_assert(
			COMPETENCY_2 in report["competencies_without_rules"],
			f"A competency with no rule linked is not reported: {report['competencies_without_rules']}",
		)
		_assert(
			any("no rule linked" in line for line in report["blocking"]),
			f"Competencies without rules are not named as blocking: {report['blocking']}",
		)

		# Validating the rule is what releases the activity, and nothing else changed.
		frappe.db.set_value("Sparsh Source of Truth Rule", draft.name, "status", "Validated")
		frappe.db.commit()
		released = seed.programme_readiness()
		_assert(
			ACTIVITY_1 not in released["auto_scoring_against_unvalidated_rules"],
			"An activity under a Validated rule is still reported as auto-scoring against an "
			"unvalidated one",
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
	finally:
		activity.reload()
		activity.evaluation_mode = original_mode
		activity.expected_response = original_expected
		activity.save(ignore_permissions=True)
		_reset_competency()
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

	_raises(reassign, "An activity with evidence was moved to another competency", expect="cannot be moved to another competency")

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

	# The ledger window is shared with every other check that records an interaction, so
	# the assertions below measure what these three fixtures added rather than asserting
	# a floor that somebody else's row already clears.
	baseline = gateway.spend(days=1)

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
	# Measured as movement across the three fixtures, not as a floor on a shared window:
	# `total_actual >= 2.0` passes on somebody else's row and says nothing about this one.
	_assert(
		report["interactions"] - baseline["interactions"] == 3,
		f"The spend report did not count exactly the three fixtures: "
		f"{report['interactions'] - baseline['interactions']}",
	)
	_assert(
		round(report["total_actual"] - (baseline["total_actual"] or 0), 6) == 2.0,
		f"The billed cost was not added to the actual total: {report['total_actual']}",
	)
	_assert(
		round(report["total_estimated"] - (baseline["total_estimated"] or 0), 6) == 1.5,
		f"The estimate was not added to the estimated total: {report['total_estimated']}",
	)
	_assert(
		report["cost_is_partly_estimated"],
		"An estimate-only interaction was reported as an actual cost",
	)
	_assert(
		report["interactions_with_no_cost_recorded"]
		- baseline["interactions_with_no_cost_recorded"] == 1,
		"The interaction with no cost at all was not reported as unknown",
	)
	_assert(
		report["without_deidentification_assertion"]
		- baseline["without_deidentification_assertion"] == 1,
		"The interaction that asserted nothing was counted as having asserted",
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
	"""Every dependency the file declares, wherever it declares one.

	Parsed with `tomllib`, and walked rather than read from a fixed list of tables. A
	regex version counted a *commented out* dependency and returned nothing at all for
	`celery[redis]>=5`; a fixed-table version still missed modern poetry groups
	(`tool.poetry.group.dev.dependencies`), PEP 735 `[dependency-groups]` and
	`build-system.requires`. Any of those is a dependency that ships.

	Walking every table named like a dependency list means a packaging convention
	invented after this was written is caught too, rather than silently passing.
	"""
	# tomllib is stdlib from 3.11, which is why `requires-python` declares 3.11 rather
	# than 3.10. The bench runs far newer; the floor is what this depends on.
	import re as _re
	import tomllib

	keys = {
		"dependencies",
		"dev-dependencies",
		"optional-dependencies",
		"dependency-groups",
		"requires",
	}
	found = []

	def walk(node):
		if isinstance(node, dict):
			for key, value in node.items():
				if key in keys:
					if isinstance(value, list):
						found.extend(value)
					elif isinstance(value, dict) and all(
						isinstance(v, str) for v in value.values()
					):
						# poetry's shape: name -> constraint string.
						found.extend(value.keys())
					elif isinstance(value, dict):
						# A table of named groups: PEP 735's `[dependency-groups]` maps
						# a group name to a list. Take the lists, not the group names --
						# and not the table name either, which is how
						# `[deploy.dependencies.apt]` was read as a package called apt.
						for grouped in value.values():
							if isinstance(grouped, list):
								found.extend(grouped)
							else:
								walk(grouped)
					continue
				walk(value)
		elif isinstance(node, list):
			for item in node:
				walk(item)

	walk(tomllib.loads(path.read_text(encoding="utf-8")))

	names = []
	for entry in found:
		if isinstance(entry, dict):
			names.extend(entry.keys())
			continue
		# "frappe[all] >= 15" / "frappe @ git+https://..." -> "frappe"
		names.append(_re.split(r"[<>=!~\[@ ;]", str(entry).strip())[0].lower())

	# `python` is a poetry interpreter constraint, not a package. Everything else counts,
	# including the build backend: a build-time dependency is still code that ships.
	return sorted({n for n in names if n} - {"python"})


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
	# `frappe` is the framework and `flit_core`/`setuptools` are build backends, which
	# do not ship into the running app. Anything else is a runtime dependency.
	permitted = {"frappe", "flit_core", "setuptools", "hatchling", "wheel"}
	unexpected = [d for d in declared if d not in permitted]
	_assert(
		not unexpected,
		f"pyproject.toml declares third-party dependencies: {unexpected}. "
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
	# A floor of four let a declared mode disappear from the Select without failing --
	# the architecture claims six evaluator types and dispatches one, so the count of
	# modes that must not score is exactly the declared total minus the dispatched ones.
	expected = len([m for m in declared if m.strip()]) - len(runner.AUTO_SCORED_MODES)
	_assert(
		len(must_not_score) == expected,
		f"{len(must_not_score)} modes must not score but {expected} were expected: {must_not_score}",
	)
	_assert(
		len([m for m in declared if m.strip()]) == 6,
		f"The Select declares {len([m for m in declared if m.strip()])} evaluator types, not six: {declared}",
	)

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
		expect="derived and cannot be deleted",
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

	_raises(edit_standing, "Certification standing was edited directly", expect="maintained by the system")

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
	"""'Who is stuck' is only useful with 'and why'.

	Built from Evidence, so this covers the evidence-derived reasons only. The learner
	with attempts and no Evidence at all is `repeated_failures_reach_the_supervisor_from_attempts`.
	"""
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
	"""The summary is counted at read time, so it cannot drift from the evidence.

	Every figure is asserted as a delta against a baseline taken on the same bench:
	`>= 1` is satisfied by whatever another check left behind, and for years only
	`certification_ready` was asserted at all -- `enrolled`, `active_in_period`,
	`require_remediation`, `certified` and `attempts_awaiting_a_person` could each have
	read zero for ever without a check going red.
	"""
	from sparsh_los import dashboard

	_reset_competency()
	# A fresh learner, because `certification_ready` counts distinct *learners*: the
	# usual subject is already ready through another competency, so demonstrating them
	# again cannot move the number and the assertion could never fail. The old fixture
	# filed one Pass -- not enough to demonstrate anybody -- and asserted
	# `certification_ready >= 1`, which that other learner already satisfied.
	_make_learner(TEST_LEARNER)
	_delete_all("Sparsh Certification Record", {"learner": TEST_LEARNER})
	_delete_all("Sparsh Evidence", {"learner": TEST_LEARNER})
	_delete_mastery({"learner": TEST_LEARNER})
	# A second fresh account for the two figures that count people rather than states:
	# it must not exist yet, or `enrolled` cannot move.
	_delete_all("Sparsh Attempt", {"learner": SUMMARY_LEARNER})
	if frappe.db.exists("User", SUMMARY_LEARNER):
		frappe.delete_doc("User", SUMMARY_LEARNER, force=True, ignore_permissions=True)
	frappe.db.commit()

	activity = frappe.get_doc("Sparsh Activity", ACTIVITY_1)
	original_mode = activity.evaluation_mode
	# Human review, so the attempt below is `Not Evaluated` with no Evidence -- the one
	# shape that counts as awaiting a person.
	activity.evaluation_mode = "Human review"
	activity.save(ignore_permissions=True)
	frappe.db.commit()

	try:
		base = dashboard.programme_summary()

		_make_learner(SUMMARY_LEARNER)
		frappe.db.commit()
		after_enrol = dashboard.programme_summary()
		_assert(
			after_enrol["enrolled"] - base["enrolled"] == 1,
			f"A new learner account did not move enrolled: {base['enrolled']} -> {after_enrol['enrolled']}",
		)
		_assert(
			after_enrol["active_in_period"] == base["active_in_period"],
			"A learner who has attempted nothing was counted as active",
		)

		attempt = _submit(ACTIVITY_1, "zzv summary attempt", as_user=SUMMARY_LEARNER)
		frappe.db.commit()
		_assert(attempt["outcome"] == "Not Evaluated", f"The fixture attempt scored {attempt['outcome']}")
		after_attempt = dashboard.programme_summary()
		_assert(
			after_attempt["active_in_period"] - base["active_in_period"] == 1,
			f"One attempt in the period did not move active_in_period: "
			f"{base['active_in_period']} -> {after_attempt['active_in_period']}",
		)
		_assert(
			after_attempt["attempts_awaiting_a_person"] - base["attempts_awaiting_a_person"] == 1,
			f"An unevaluated attempt did not move attempts_awaiting_a_person: "
			f"{base['attempts_awaiting_a_person']} -> {after_attempt['attempts_awaiting_a_person']}",
		)

		_new_evidence(ACTIVITY_1, "Pass", assistance_level=0, learner=TEST_LEARNER)
		_new_evidence(ACTIVITY_2, "Pass", assistance_level=0, learner=TEST_LEARNER)
		frappe.db.commit()
		_assert(
			frappe.db.get_value(
				"Sparsh Mastery State", {"learner": TEST_LEARNER, "competency": COMPETENCY}, "state"
			) in (DEMONSTRATED, MASTERED),
			"The fixture learner was not demonstrated, so the ready count cannot be tested",
		)

		summary = dashboard.programme_summary()
		_assert(
			summary["certification_ready"] - base["certification_ready"] == 1,
			f"The demonstrated learner did not move the ready count: "
			f"{base['certification_ready']} -> {summary['certification_ready']}",
		)
		_assert("most_common_gap" in summary, "The summary names no most-common gap")
		_assert(summary["period_days"] == 30, "The default period is not 30 days")

		# Practising on a second competency: one more learner needing remediation.
		# No activity: the provenance rule applies to unaided passes, not to an assisted one.
		_new_evidence(None, "Pass", competency=COMPETENCY_2, assistance_level=1, learner=TEST_LEARNER)
		frappe.db.commit()
		_assert(
			frappe.db.get_value(
				"Sparsh Mastery State", {"learner": TEST_LEARNER, "competency": COMPETENCY_2}, "state"
			) == "Practising",
			"The fixture learner is not Practising, so remediation cannot be tested",
		)
		summary = dashboard.programme_summary()
		_assert(
			summary["require_remediation"] - base["require_remediation"] == 1,
			f"A Practising learner did not move require_remediation: "
			f"{base['require_remediation']} -> {summary['require_remediation']}",
		)
		_assert(
			COMPETENCY_2 in summary["gaps"],
			f"A competency short of demonstration is not a gap: {summary['gaps']}",
		)

		certificate = frappe.new_doc("Sparsh Certification Record")
		certificate.learner = TEST_LEARNER
		certificate.competency = COMPETENCY
		certificate.certification_status = "Full"
		certificate.insert(ignore_permissions=True)
		certificate.submit()
		frappe.db.commit()
		summary = dashboard.programme_summary()
		_assert(
			summary["certified"] - base["certified"] == 1,
			f"An active certification did not move certified: {base['certified']} -> {summary['certified']}",
		)

		# It is a supervisor view.
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
	finally:
		activity.reload()
		activity.evaluation_mode = original_mode
		activity.save(ignore_permissions=True)
		_delete_all("Sparsh Attempt", {"learner": SUMMARY_LEARNER})
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
			expect="historical record and cannot be deleted",
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


# ------------------------------------------------------- audit 17: closing the gaps
def check_escalation_cannot_be_self_answered_by_update():
	"""The self-answer guard has three doors; the harness only ever tried two.

	`check_nobody_judges_their_own_work` exercised Evidence, Certification and Human
	Review, and `escalation.answer` was covered at the endpoint. Nothing tried an
	ordinary save on an Escalation Question, which is the route a dual-role user has.
	"""
	from sparsh_los import escalation

	_make_learner(DUAL_LEARNER)
	user = frappe.get_doc("User", DUAL_LEARNER)
	if "Sparsh Reviewer" not in [r.role for r in user.roles]:
		user.append("roles", {"role": "Sparsh Reviewer"})
		user.save(ignore_permissions=True)
	frappe.db.commit()

	_delete_all("Sparsh Escalation Question", {"learner": DUAL_LEARNER})

	original = frappe.session.user
	try:
		frappe.set_user(DUAL_LEARNER)
		name = escalation.raise_question("A question I intend to answer myself", activity=ACTIVITY_1)
		frappe.db.commit()

		def answer_by_saving():
			doc = frappe.get_doc("Sparsh Escalation Question", name)
			doc.status = "Answered"
			doc.answer_text = "I am satisfied with my own answer."
			doc.answered_by = DUAL_LEARNER
			doc.disposition = "Private answer"
			doc.save()

		# `_raises` only admits frappe.ValidationError, and PermissionError is not one of
		# its subclasses on this version, so the refusal has to be caught for what it is.
		frappe.db.savepoint("sparsh_selfanswer")
		try:
			answer_by_saving()
		except frappe.PermissionError as exc:
			frappe.db.rollback(save_point="sparsh_selfanswer")
			_assert("your own question" in str(exc).lower(),
					f"Refused, but for another reason: {exc}")
		else:
			frappe.db.rollback(save_point="sparsh_selfanswer")
			raise AssertionError("A dual-role user answered their own question by saving it")
	finally:
		frappe.set_user(original)

	_assert(
		frappe.db.get_value("Sparsh Escalation Question", name, "status") == "Open",
		"The question did not stay Open after the refused self-answer",
	)

	# Setting `answered_by` to themselves is the one branch the first guard detected.
	# A dual-role learner would name somebody else, and the guard -- which asked the
	# document who answered it -- saw a different name and allowed it.
	_make_learner(OTHER_LEARNER)
	other = frappe.get_doc("User", OTHER_LEARNER)
	if "Sparsh Reviewer" not in [r.role for r in other.roles]:
		other.append("roles", {"role": "Sparsh Reviewer"})
		other.save(ignore_permissions=True)
	frappe.db.commit()

	try:
		frappe.set_user(DUAL_LEARNER)

		def answer_but_blame_somebody_else():
			doc = frappe.get_doc("Sparsh Escalation Question", name)
			doc.status = "Answered"
			doc.answer_text = "Answered by me, attributed to a colleague."
			doc.answered_by = OTHER_LEARNER
			doc.disposition = "Private answer"
			doc.save()

		frappe.db.savepoint("sparsh_spoof")
		try:
			answer_but_blame_somebody_else()
		except frappe.PermissionError as exc:
			frappe.db.rollback(save_point="sparsh_spoof")
			_assert("your own question" in str(exc).lower(),
					f"Refused, but for another reason: {exc}")
		else:
			frappe.db.rollback(save_point="sparsh_spoof")
			raise AssertionError(
				"A learner answered their own question and attributed it to somebody else"
			)
	finally:
		frappe.set_user(original)

	# A real reviewer answers it, and the learner must not be able to rewrite what they
	# were told -- the stale `answered_by` made that edit look like the reviewer's work.
	try:
		frappe.set_user(OTHER_LEARNER)
		escalation.answer(name, "The reviewer's actual answer.", "Private answer")
		frappe.db.commit()
	finally:
		frappe.set_user(original)

	stored = frappe.db.get_value(
		"Sparsh Escalation Question", name, ["answered_by", "answer_text"], as_dict=True
	)
	_assert(
		stored.answered_by == OTHER_LEARNER,
		f"The answer was attributed to {stored.answered_by}, not the reviewer who wrote it",
	)

	try:
		frappe.set_user(DUAL_LEARNER)

		def rewrite_the_answer():
			doc = frappe.get_doc("Sparsh Escalation Question", name)
			doc.answer_text = "Something the reviewer never said."
			doc.save()

		frappe.db.savepoint("sparsh_rewrite")
		try:
			rewrite_the_answer()
		except frappe.PermissionError as exc:
			frappe.db.rollback(save_point="sparsh_rewrite")
			_assert("already been answered" in str(exc).lower(),
					f"Refused, but for another reason: {exc}")
		else:
			frappe.db.rollback(save_point="sparsh_rewrite")
			raise AssertionError("A learner rewrote the answer a reviewer had given them")
	finally:
		frappe.set_user(original)

	_assert(
		frappe.db.get_value("Sparsh Escalation Question", name, "answer_text")
		== "The reviewer's actual answer.",
		"The stored answer is not what the reviewer wrote",
	)

	# The two-save bypass: point the question at somebody else, answer it as yourself
	# while the comparison is false, then point it back. Neither save compared the
	# session user against the real owner, because `learner` was part of the payload.
	fresh = None
	try:
		frappe.set_user(DUAL_LEARNER)
		fresh = escalation.raise_question("A second question I intend to answer", activity=ACTIVITY_1)
		frappe.db.commit()

		def reassign_then_answer():
			doc = frappe.get_doc("Sparsh Escalation Question", fresh)
			doc.learner = OTHER_LEARNER
			doc.status = "Answered"
			doc.answer_text = "Answered while the question pointed at somebody else."
			doc.disposition = "Private answer"
			doc.save()

		frappe.db.savepoint("sparsh_reassign")
		try:
			reassign_then_answer()
		except frappe.PermissionError as exc:
			frappe.db.rollback(save_point="sparsh_reassign")
			_assert("belongs to the learner" in str(exc).lower(),
					f"Refused, but for another reason: {exc}")
		else:
			frappe.db.rollback(save_point="sparsh_reassign")
			raise AssertionError(
				"A learner reassigned their own question and answered it"
			)
	finally:
		frappe.set_user(original)

	_assert(
		frappe.db.get_value("Sparsh Escalation Question", fresh, "learner") == DUAL_LEARNER,
		"The question changed hands",
	)

	# The freeze is stated as "every field except the framework's own bookkeeping", but
	# the cases above only ever change `learner` and `answer_text`. A guard narrowed to
	# a list of the fields the harness happens to try would still pass all of them, so
	# every writable field on the answered question is tried in turn.
	answered = frappe.get_doc("Sparsh Escalation Question", name)
	bookkeeping = {"modified", "modified_by", "_user_tags", "_comments", "_assign",
				   "_liked_by", "idx", "docstatus", "name", "owner", "creation",
				   "doctype", "parent", "parentfield", "parenttype"}
	meta = frappe.get_meta("Sparsh Escalation Question")
	unfrozen = []
	for field in meta.fields:
		if field.fieldname in bookkeeping or field.fieldtype in frappe.model.no_value_fields:
			continue
		current = answered.get(field.fieldname)
		if field.fieldtype in ("Datetime", "Date"):
			altered = frappe.utils.add_to_date(frappe.utils.now_datetime(), days=-3)
		elif field.fieldtype == "Select":
			options = [o for o in (field.options or "").split("\n") if o and o != current]
			if not options:
				continue
			altered = options[0]
		elif field.fieldtype in ("Link", "Data", "Small Text", "Text", "Long Text", "Text Editor"):
			altered = "zzv-frozen-field-probe"
		elif field.fieldtype in ("Int", "Float", "Check"):
			altered = (current or 0) + 1
		else:
			continue
		if altered == current:
			continue

		frappe.db.savepoint("sparsh_frozen")
		try:
			doc = frappe.get_doc("Sparsh Escalation Question", name)
			doc.set(field.fieldname, altered)
			doc.save(ignore_permissions=True)
		except frappe.PermissionError:
			pass
		except frappe.ValidationError:
			# A link that does not resolve, a mandatory field emptied: refused by another
			# layer, so this field says nothing either way about the freeze.
			pass
		else:
			unfrozen.append(field.fieldname)
		finally:
			frappe.db.rollback(save_point="sparsh_frozen")

	_assert(
		not unfrozen,
		f"An answered question accepted a change to {unfrozen}, so the freeze is a list "
		f"of the fields the harness tries rather than the guarantee it claims",
	)

	# A freeze that can be stepped around by deleting the row protects nothing. The
	# System Manager DocPerm carries `delete`, and deletion never reaches `validate`.
	frappe.db.savepoint("sparsh_delete_answered")
	try:
		frappe.delete_doc("Sparsh Escalation Question", name, force=True, ignore_permissions=True)
	except frappe.PermissionError as exc:
		frappe.db.rollback(save_point="sparsh_delete_answered")
		_assert("cannot be deleted" in str(exc).lower(),
				f"Refused, but for another reason: {exc}")
	else:
		frappe.db.rollback(save_point="sparsh_delete_answered")
		raise AssertionError(
			"An answered question was deleted, which removes the answer the freeze protects"
		)
	_assert(
		frappe.db.get_value("Sparsh Escalation Question", fresh, "status") == "Open",
		"The reassigned question was answered anyway",
	)

	# And the rest of an answered record is frozen too: rewriting the question after
	# the fact makes the stored answer address something the reviewer never saw.
	try:
		frappe.set_user(OTHER_LEARNER)

		def rewrite_the_question():
			doc = frappe.get_doc("Sparsh Escalation Question", name)
			doc.question_text = "A different question entirely."
			doc.save()

		frappe.db.savepoint("sparsh_requestion")
		try:
			rewrite_the_question()
		except frappe.PermissionError:
			frappe.db.rollback(save_point="sparsh_requestion")
		else:
			frappe.db.rollback(save_point="sparsh_requestion")
			raise AssertionError("The question behind an answer was rewritten after the fact")
	finally:
		frappe.set_user(original)

	_delete_all("Sparsh Escalation Question", {"learner": OTHER_LEARNER})
	_delete_all("Sparsh Escalation Question", {"learner": DUAL_LEARNER})
	frappe.db.commit()


def check_no_rule_auto_scoring_blocks_the_pilot():
	"""An activity with no rule linked scores itself, so it cannot read as pilot-safe.

	The existing readiness check built a Draft *linked* rule. The one configuration
	`_rule_is_validated` lets through -- no rule at all -- was named in the report and
	excluded from the verdict, so the same document said both things.
	"""
	from sparsh_los import seed

	_delete_all("Sparsh Activity", {"activity_id": OTHER_ACTIVITY})
	# `other_domain_runs_unchanged` builds this competency itself and inserts without
	# checking, so anything created here has to be taken away again. It also links a
	# governing rule now that the gate fails closed, and this check's whole subject is
	# the competency with no rule, so the link has to come off first.
	borrowed = not frappe.db.exists("Sparsh Competency", OTHER_COMPETENCY)
	if not borrowed:
		_unlink_rules(OTHER_COMPETENCY)
	if borrowed:
		competency = frappe.new_doc("Sparsh Competency")
		competency.competency_id = OTHER_COMPETENCY
		competency.competency_name = "Verification Competency"
		competency.domain = DOMAIN
		competency.insert(ignore_permissions=True)
	frappe.db.commit()

	activity = frappe.new_doc("Sparsh Activity")
	activity.activity_id = OTHER_ACTIVITY
	activity.title = "Verification Activity"
	activity.competency = OTHER_COMPETENCY
	activity.activity_type = "Knowledge check"
	activity.instruction = "Verification instruction."
	activity.version = 1
	activity.evaluation_mode = "Deterministic"
	activity.expected_response = "the expected answer"
	activity.insert(ignore_permissions=True)
	frappe.db.commit()

	_assert(
		not frappe.get_all("Sparsh Competency Rule Link", filters={"parent": OTHER_COMPETENCY}, limit=1),
		"The fixture competency has a rule linked, so this proves nothing",
	)

	# Ambient blockers have to be cleared or the verdict is False whatever the fixture
	# does. The only ones on a verification site are the harness's own competencies,
	# which have no activity; a real one without an activity means this check cannot
	# discriminate and should say so rather than pass.
	stand_ins = []
	muted = []
	baseline = seed.programme_readiness()
	baseline_gaps = baseline["competencies_without_activities"]

	# The harness's own fixture activities default to Deterministic and link no rule, so
	# they are no-rule auto-scorers too and hold the verdict False by themselves. They
	# are muted for the duration and restored in the finally.
	for name in baseline["auto_scoring_with_no_rule_linked"]:
		if name == activity.name:
			# The fixture under test stays exactly as it is; muting it would hide the
			# very configuration this check exists to catch.
			continue
		activity_id = frappe.db.get_value("Sparsh Activity", name, "activity_id") or ""
		_assert(
			activity_id.startswith(PREFIX),
			f"{name} auto-scores with no rule linked, so the pilot verdict is False "
			"regardless and this check cannot discriminate",
		)
		muted.append(name)
		frappe.db.set_value("Sparsh Activity", name, "evaluation_mode", "Human review")
	frappe.db.commit()
	for competency_id in baseline_gaps:
		_assert(
			competency_id.startswith(PREFIX),
			f"{competency_id} has no activity, so the pilot verdict is False regardless "
			"and this check cannot discriminate",
		)
		stand_in = frappe.new_doc("Sparsh Activity")
		stand_in.activity_id = PREFIX + "STANDIN-" + competency_id
		stand_in.title = "Verification Activity"
		stand_in.competency = competency_id
		stand_in.activity_type = "Knowledge check"
		stand_in.instruction = "Verification instruction."
		stand_in.version = 1
		# Explicit: a stand-in left on the default mode becomes a no-rule auto-scorer
		# itself and blocks the very verdict this check is trying to isolate.
		stand_in.evaluation_mode = "Human review"
		stand_in.insert(ignore_permissions=True)
		stand_ins.append(stand_in.activity_id)
	frappe.db.commit()

	try:
		readiness = seed.programme_readiness()
		_assert(
			activity.name in readiness["auto_scoring_with_no_rule_linked"],
			"A Deterministic activity with no rule was not reported",
		)
		_assert(
			not readiness["can_pilot_with_human_review"],
			"Readiness called the pilot human-review-safe while naming a no-rule auto-scorer",
		)

		# Asserting the verdict is False proves nothing on a site where something else
		# already blocks the pilot -- and on this one, something did: the first version of
		# this check passed with the fix reverted. The activity has to be shown to be the
		# cause. Its mode is flipped rather than the activity deleted, because deleting it
		# leaves its competency with no activity at all, which blocks the pilot for a
		# different reason and makes the comparison meaningless.
		frappe.db.set_value("Sparsh Activity", activity.name, "evaluation_mode", "Human review")
		frappe.db.commit()
		without = seed.programme_readiness()
		_assert(
			without["can_pilot_with_human_review"],
			"The pilot is blocked by something other than the fixture, so this check "
			f"cannot discriminate: gaps={without['competencies_without_activities']} "
			f"unvalidated={without['auto_scoring_against_unvalidated_rules']} "
			f"no_rule={without['auto_scoring_with_no_rule_linked']}",
		)
	finally:
		for name in muted:
			frappe.db.set_value("Sparsh Activity", name, "evaluation_mode", "Deterministic")
		_delete_all("Sparsh Activity", {"activity_id": OTHER_ACTIVITY})
		for activity_id in stand_ins:
			_delete_all("Sparsh Activity", {"activity_id": activity_id})
		if borrowed:
			_delete_all("Sparsh Competency", {"competency_id": OTHER_COMPETENCY})
		frappe.db.commit()


def check_resource_lineage_is_checked():
	"""Supersession retires the predecessor, so the link has to mean what it says.

	The existing resource check built a valid lineage and asserted history survived it.
	It never tried an invalid one, and the controller validated nothing but
	self-supersession.
	"""
	_delete_all("Sparsh Learning Resource", {"resource_id": ("like", PREFIX + "LIN%")})
	frappe.db.commit()

	def _resource(resource_id, version, status="Draft", supersedes=None):
		doc = frappe.new_doc("Sparsh Learning Resource")
		doc.resource_id = resource_id
		doc.version = version
		doc.title = "Lineage fixture"
		doc.resource_type = "Explainer"
		doc.status = status
		doc.supersedes = supersedes
		doc.insert(ignore_permissions=True)
		return doc

	first = _resource(PREFIX + "LIN-A", 1, status="Current")
	stranger = _resource(PREFIX + "LIN-B", 1, status="Current")
	frappe.db.commit()

	_raises(
		lambda: _resource(PREFIX + "LIN-B", 2, supersedes=first.name),
		"A resource superseded an unrelated resource",
		expect="another version of itself",
	)
	later = _resource(PREFIX + "LIN-A", 5)
	frappe.db.commit()
	_raises(
		lambda: _resource(PREFIX + "LIN-A", 3, supersedes=later.name),
		"A resource superseded a version that is not earlier than it",
		expect="comes after",
	)
	_raises(
		lambda: _resource(PREFIX + "LIN-A", 3, status="Current"),
		"A second Current version was allowed for one resource",
		expect="already the current version",
	)

	# The legitimate promotion must still work, or the guard has only broken the app.
	successor = _resource(PREFIX + "LIN-A", 2, status="Current", supersedes=first.name)
	frappe.db.commit()
	_assert(
		frappe.db.get_value("Sparsh Learning Resource", first.name, "status") == "Superseded",
		"A valid supersession did not retire its predecessor",
	)
	_assert(successor.status == "Current", "A valid successor did not become Current")

	# The index constrains `current_key`; nothing asserted the field was ever set, so a
	# regression where no Current version claimed a key passed every check above.
	_assert(
		frappe.db.get_value("Sparsh Learning Resource", successor.name, "current_key")
		== PREFIX + "LIN-A",
		"The Current version does not hold the key the index constrains",
	)
	_assert(
		frappe.db.get_value("Sparsh Learning Resource", first.name, "current_key") is None,
		"The superseded version still holds the key",
	)

	# An ordinary edit to a Current resource must not release the key. It did: validation
	# cleared it, and `on_update` restored it only on a transition, so the row stayed
	# Current holding nothing and the next version could claim the key beside it.
	successor.reload()
	successor.title = "Lineage fixture, retitled"
	successor.save(ignore_permissions=True)
	frappe.db.commit()
	_assert(
		frappe.db.get_value("Sparsh Learning Resource", successor.name, "current_key")
		== PREFIX + "LIN-A",
		"An ordinary edit released the Current version's key",
	)
	_raises(
		lambda: _resource(PREFIX + "LIN-A", 9, status="Current"),
		"A second Current version was allowed after the first was edited",
		expect="already the current version",
	)

	# The query above loses a race between two successors of the same predecessor: both
	# see one Current version, both exempt it, both insert. Only the database can settle
	# that, so the constraint -- not the check -- is the guarantee being verified here.
	indexes = frappe.db.sql(
		"""show index from `tabSparsh Learning Resource` where Key_name = 'unique_current_resource'""",
		as_dict=True,
	)
	_assert(indexes, "There is no unique index on current_key, so two versions can race to Current")
	_assert(
		all(row.Non_unique == 0 for row in indexes),
		"The current_key index exists but is not unique",
	)
	# The name alone proves nothing: a unique index over some other column, carrying this
	# constraint name, satisfied both assertions above.
	_assert(
		[row.Column_name for row in indexes] == ["current_key"],
		f"unique_current_resource covers {[row.Column_name for row in indexes]}, "
		f"not current_key alone",
	)

	_delete_all("Sparsh Learning Resource", {"resource_id": ("like", PREFIX + "LIN%")})
	frappe.db.commit()


def check_blank_currency_suppresses_totals():
	"""Every priced row unlabelled is not a single-currency ledger.

	The mixed-currency fixture put INR beside USD, so `currencies` was never empty and
	the all-blank branch -- the one the comment above the code describes -- never ran.
	"""
	from sparsh_los import gateway

	_delete_all("Sparsh Model Interaction", {"provider": "zzv-blank"})
	frappe.db.commit()

	blank = gateway.record(
		provider="zzv-blank", model_id="zzv-model-blank", purpose="Other",
		actual_cost=12.5, deidentified=1,
	)
	# The field defaults to INR, so the unlabelled case cannot be reached through
	# record() -- which is why it had no fixture and the branch was never exercised.
	frappe.db.set_value("Sparsh Model Interaction", blank, "cost_currency", "")
	frappe.db.commit()

	# Cleanup in a finally: when this check failed, its unlabelled row stayed in the
	# ledger and took the model-ledger check down with it on the next run.
	try:
		report = gateway.spend(days=1)
		_assert(
			report["priced_interactions_without_a_currency"] >= 1,
			"The unlabelled priced row was not counted",
		)
		# Without this the check passes on the *labelled* row somebody else recorded in
		# the same window: `bool(currencies) and unlabelled` is true then too, so the
		# old expression would have been reported as fixed.
		_assert(
			report["currencies"] == [],
			f"Another priced row carries a currency, so this window cannot test the "
			f"all-blank case: {report['currencies']}",
		)
		_assert(
			report["mixed_currency"],
			"A ledger whose only priced rows carry no currency was reported as single-currency",
		)
		for key in ("total_cost", "total_actual", "total_estimated", "cost_per_learner"):
			_assert(
				report[key] is None,
				f"{key} was reported as a number in an unknown currency: {report[key]}",
			)
		_assert(report["currency"] is None, "A currency was named when no row carried one")
	finally:
		_delete_all("Sparsh Model Interaction", {"provider": "zzv-blank"})
		frappe.db.commit()


def check_supervisor_ignores_rejected_evidence():
	"""The explanation must be derived from the same rows as the state it explains.

	`derive_state` drops rejected evidence; the dashboard counted it, so a learner held
	back by nothing could be explained as "Passing only with help".
	"""
	from sparsh_los import dashboard

	_reset_competency()
	for _ in range(3):
		evidence = _new_evidence(ACTIVITY_1, "Pass", assistance_level=1)
		frappe.db.set_value("Sparsh Evidence", evidence.name, "human_review_status", "Rejected")
	frappe.db.commit()

	# The learner is still Practising with three evidence rows, so appearing here is
	# correct. What must not happen is the explanation citing the rejected passes.
	rows = [r for r in dashboard.supervisor_view(COMPETENCY)["stuck"] if r["learner"] == LEARNER]
	# Asserting inside the loop alone is vacuous: if the learner ever stops appearing,
	# every assertion below is skipped and the check reports success.
	_assert(rows, "The learner is absent from the stuck list, so nothing below was tested")
	for row in rows:
		_assert(
			row["assisted_passes"] == 0,
			f"Rejected assisted passes were counted: {row['assisted_passes']}",
		)
		_assert(
			row["reason"] != "Passing only with help",
			"A learner whose every pass was rejected was explained as passing with help",
		)

	_reset_competency()
	frappe.db.commit()



def check_unenrolled_learns_nothing_from_the_error():
	"""The refusal must not depend on whether the activity exists.

	`runner.submit` looked the activity up before the enrolment gate, so an unenrolled
	account got "no such activity" for a made-up name and an enrolment error for a real
	one -- enough to enumerate the catalogue one guess at a time.
	"""
	from sparsh_los import runner

	_make_learner(OTHER_LEARNER)
	frappe.db.set_value("User", OTHER_LEARNER, "enabled", 1)
	user = frappe.get_doc("User", OTHER_LEARNER)
	for row in list(user.roles):
		if row.role in ("Sparsh Learner", "Sparsh Reviewer"):
			user.remove(row)
	user.save(ignore_permissions=True)
	frappe.db.commit()

	original = frappe.session.user
	errors = {}
	try:
		frappe.set_user(OTHER_LEARNER)
		for label, name in (("real", ACTIVITY_1), ("invented", PREFIX + "NO-SUCH-ACTIVITY")):
			frappe.db.savepoint("sparsh_oracle")
			try:
				runner.submit(name, "a response")
			except Exception as exc:  # noqa: BLE001 - the message is the subject
				errors[label] = f"{type(exc).__name__}: {exc}"
			finally:
				frappe.db.rollback(save_point="sparsh_oracle")
	finally:
		frappe.set_user(original)

	_assert(len(errors) == 2, f"An unenrolled submit was not refused at all: {errors}")
	_assert(
		errors["real"] == errors["invented"],
		f"The refusal revealed whether the activity exists: {errors}",
	)
	_assert("not enrolled" in errors["real"].lower(), f"Refused for another reason: {errors}")



def check_queue_limit_is_bounded():
	"""`limit` is caller input, and it reached the query as int(limit) or an exception."""
	from sparsh_los import review

	original = frappe.session.user
	try:
		frappe.set_user(LEARNER)
		frappe.get_doc("User", LEARNER)
	finally:
		frappe.set_user(original)

	_raises(lambda: review.pending(limit=0), "A zero queue limit was accepted",
			expect="between 1 and")
	_raises(lambda: review.pending(limit=-5), "A negative queue limit was accepted",
			expect="between 1 and")
	_raises(lambda: review.pending(limit=10 ** 9), "An unbounded queue limit was accepted",
			expect="between 1 and")
	_raises(lambda: review.pending(limit="all of them"),
			"A malformed queue limit raised instead of being rejected",
			expect="whole number")

	# The ordinary call still works, or the bound has only broken the queue.
	_assert(isinstance(review.pending(limit=5), list), "A valid queue limit was refused")


def check_refresh_due_advice_names_the_refresher():
	"""A learner held by a refresher may already have every unaided pass required.

	The readiness text told every non-Demonstrated learner to earn an unaided pass,
	which sends a Refresh Due learner at the wrong task and reads as though their
	earlier evidence had stopped counting.
	"""
	from sparsh_los import certification, refresher
	from sparsh_los.mastery import recompute_mastery

	_reset_competency()
	rule = _new_rule(1)
	_new_evidence(ACTIVITY_1, "Pass", assistance_level=0)
	_new_evidence(ACTIVITY_2, "Pass", assistance_level=0)
	frappe.db.commit()

	_assert(_state() in (DEMONSTRATED, MASTERED), f"The fixture did not demonstrate: {_state()}")

	refresher.assign(LEARNER, COMPETENCY, reason="Rule changed", detail=rule.name)
	recompute_mastery(LEARNER, COMPETENCY)
	frappe.db.commit()
	_assert(_state() == "Refresh Due", f"The fixture is not Refresh Due: {_state()}")

	report = certification.readiness(COMPETENCY, learner=LEARNER)
	_assert(
		"refresher" in report["reason"].lower(),
		f"Refresh Due advice does not mention the refresher: {report['reason']}",
	)
	_assert(
		"unaided pass is needed" not in report["reason"],
		f"A Refresh Due learner was told to earn an unaided pass: {report['reason']}",
	)

	_reset_competency()
	frappe.db.commit()


def check_cross_user_endpoints_refuse():
	"""Every endpoint taking a learner argument is an access decision.

	`check_whitelisted_reads_are_scoped` covered most of them and omitted these two, so
	removing either owner check would not have failed anything.
	"""
	from sparsh_los import orchestrator
	from sparsh_los.sparsh_los.doctype.sparsh_certification_record import (
		sparsh_certification_record,
	)

	_make_learner(TEST_LEARNER)
	_make_learner(OTHER_LEARNER)
	frappe.db.commit()

	original = frappe.session.user
	try:
		frappe.set_user(TEST_LEARNER)
		for label, call in (
			("certification_record.current",
			 lambda: sparsh_certification_record.current(OTHER_LEARNER, COMPETENCY)),
			("orchestrator.next_in_pathway",
			 lambda: orchestrator.next_in_pathway(PATHWAY, learner=OTHER_LEARNER)),
		):
			frappe.db.savepoint("sparsh_cross")
			try:
				call()
			except frappe.PermissionError:
				frappe.db.rollback(save_point="sparsh_cross")
				continue
			except Exception as exc:  # noqa: BLE001
				frappe.db.rollback(save_point="sparsh_cross")
				raise AssertionError(
					f"{label} refused another learner for the wrong reason: "
					f"{type(exc).__name__}: {exc}"
				) from None
			frappe.db.rollback(save_point="sparsh_cross")
			raise AssertionError(f"{label} answered about another learner")
	finally:
		frappe.set_user(original)


def check_no_programme_name_in_messages():
	"""The engine is domain-agnostic, and a message is as user-facing as a label.

	`check_no_domain_strings` reads DocType names and field metadata. The invariant also
	covers validations, and nothing looked at the strings a user actually reads. The
	DocType prefix `Sparsh ` is the deliberate exception -- it is the app namespace, not
	the programme -- so what is banned here is the programme's own name.

	The first version matched a line containing both the name and `_("` or
	`frappe.throw`, which saw only double-quoted single-line calls: a single-quoted
	string, a name on the second line of a multiline message, or a message built into a
	variable and thrown later all passed. It walks the syntax tree now, so the quoting
	style and the line breaks stop mattering.
	"""
	import ast as _ast
	import os
	import re

	root = os.path.dirname(os.path.abspath(__file__))
	programme = re.compile(r"sai[\s_-]*sparsh", re.IGNORECASE)
	offenders = []
	for dirpath, _dirs, filenames in os.walk(root):
		for filename in filenames:
			if not filename.endswith(".py"):
				continue
			path = os.path.join(dirpath, filename)
			try:
				with open(path, encoding="utf-8") as handle:
					tree = _ast.parse(handle.read())
			except (UnicodeDecodeError, OSError, SyntaxError):
				# A stray byte or an unparseable file is not this check's business, and
				# failing on it would report a scan error as a domain-string violation.
				continue

			# Docstrings explain the project to the next reader and ship to nobody.
			docstrings = set()
			for node in _ast.walk(tree):
				if isinstance(node, (_ast.Module, _ast.ClassDef, _ast.FunctionDef,
									 _ast.AsyncFunctionDef)):
					body = getattr(node, "body", None)
					if (
						body
						and isinstance(body[0], _ast.Expr)
						and isinstance(body[0].value, _ast.Constant)
						and isinstance(body[0].value.value, str)
					):
						docstrings.add(id(body[0].value))

			for node in _ast.walk(tree):
				if not (isinstance(node, _ast.Constant) and isinstance(node.value, str)):
					continue
				if id(node) in docstrings:
					continue
				if programme.search(node.value):
					offenders.append(
						f"{os.path.relpath(path, root)}:{node.lineno}: {node.value[:60]!r}"
					)

	_assert(not offenders, f"The programme name appears in a string: {offenders}")

def check_queue_shows_work_that_is_actually_waiting():
	"""The limit must apply to eligible rows, not to rows then thrown away.

	`pending` took the oldest `limit` attempts and *then* discarded those with evidence
	or from another competency, so a handful of old reviewed attempts returned an empty
	queue while unreviewed work sat behind them. The queue starved without saying so.
	"""
	from sparsh_los import review

	_reset_competency()
	rule = _new_rule(1)

	# What this site already has waiting, before any fixture of ours exists.
	already_waiting = len(review.pending(limit=review.MAX_QUEUE))

	# Two older attempts that are not eligible, because a reviewer has already judged
	# them, followed by one that is.
	for _ in range(2):
		attempt = _new_attempt(rule.name, outcome="Not Evaluated")
		evidence = _new_evidence(ACTIVITY_1, "Pass")
		frappe.db.set_value("Sparsh Evidence", evidence.name, "attempt", attempt.name)
	frappe.db.commit()

	# The other-competency attempt is created *before* the one being looked for, so it
	# is genuinely older. Created after, it sorted behind the waiting attempt and a
	# query that filtered the competency only after limiting still passed.
	other_activity_id = PREFIX + "QUEUE-OTHER"
	_delete_all("Sparsh Activity", {"activity_id": other_activity_id})
	other_activity = frappe.new_doc("Sparsh Activity")
	other_activity.activity_id = other_activity_id
	other_activity.title = "Verification Activity"
	other_activity.competency = COMPETENCY_2
	other_activity.activity_type = "Knowledge check"
	other_activity.instruction = "Verification instruction."
	other_activity.version = 1
	other_activity.evaluation_mode = "Human review"
	other_activity.insert(ignore_permissions=True)
	# Two of them, and the scoped call asks for two: a query that filters the competency
	# only after limiting spends both slots on these and returns nothing, while a query
	# that filters first never sees them. One foreign row was not enough -- it left a
	# slot free, so the starved and the correct query returned the same thing.
	other_attempt = _new_attempt(rule.name, outcome="Not Evaluated", activity=other_activity.name)
	second_other = _new_attempt(rule.name, outcome="Not Evaluated", activity=other_activity.name)
	frappe.db.commit()

	waiting_attempt = _new_attempt(rule.name, outcome="Not Evaluated")
	frappe.db.commit()

	scoped = review.pending(competency=COMPETENCY, limit=2)
	_assert(
		not any(row["name"] in (other_attempt.name, second_other.name) for row in scoped),
		"A competency-scoped queue returned an attempt from another competency",
	)
	# Without this, a scoped queue that returns nothing at all satisfies the assertion
	# above and the check passes on an empty result.
	_assert(
		any(row["name"] == waiting_attempt.name for row in scoped),
		"The competency-scoped queue omitted the attempt waiting in that competency",
	)

	# Four slots beyond whatever was already waiting on this site, of which the two
	# foreign-competency rows take two: under the old ordering the two *reviewed*
	# attempts took the earliest slots as well and the one genuinely waiting was pushed
	# out of the window entirely.
	#
	# The four used to be an absolute limit, which silently assumed the site held no
	# other unreviewed work. It does once the demonstration cohort is loaded -- six
	# attempts, all older than these fixtures -- and they took every slot, so this check
	# failed for want of room rather than for the defect it watches. Measured as a delta
	# from what is already queued, exactly as this project requires of every assertion.
	queue = review.pending(limit=already_waiting + 4)
	_assert(
		any(row["name"] == waiting_attempt.name for row in queue),
		"An attempt waiting for review was hidden behind older attempts that were "
		"already judged",
	)
	_delete_all("Sparsh Attempt", {"activity": other_activity.name})
	_delete_all("Sparsh Activity", {"activity_id": other_activity_id})
	_reset_competency()
	frappe.db.commit()


def check_ledger_with_no_price_reports_unknown_not_zero():
	"""No cost recorded anywhere is not zero spend.

	`total_cost: 0.0` reads as "cheap"; the comment beside the code said exactly that
	while the code still returned the zero whenever no row carried a price at all.
	"""
	from sparsh_los import gateway

	_delete_all("Sparsh Model Interaction", {"provider": "zzv-nocost"})
	frappe.db.commit()

	try:
		unpriced = gateway.record(
			provider="zzv-nocost", model_id="zzv-model-nocost", purpose="Other", deidentified=1
		)
		frappe.db.commit()

		report = gateway.spend(days=1)
		_assert(
			report["interactions"] >= 1,
			"The fixture interaction is not in the reporting window",
		)
		if report["priced_interactions_without_a_currency"] or report["currencies"]:
			# Another check's priced row is in the window; this one cannot discriminate
			# and says so rather than passing on somebody else's data.
			raise AssertionError(
				"A priced row from another check is in the window, so the no-price case "
				"cannot be tested here"
			)
		for key in ("total_cost", "total_actual", "total_estimated"):
			_assert(
				report[key] is None,
				f"{key} was reported as {report[key]} when no interaction carried a price",
			)
		_assert(
			report["interactions_with_no_cost_recorded"] >= 1,
			"The costless interaction was not counted as unknown",
		)
		_assert(
			report["total_covers_every_interaction"] is False,
			"A period containing an unpriced interaction claimed its totals were complete",
		)

		# The assertion above holds for a window where *nothing* is priced, which is also
		# what `not priced` would report -- and `not priced` is wrong for the case the
		# flag exists to explain: some rows priced, some not, so the total is a real
		# subtotal rather than nothing at all. The guard above has already established
		# that this window holds only this check's rows, so the mixed case can be built.
		gateway.record(
			provider="zzv-nocost", model_id="zzv-model-priced", purpose="Other",
			deidentified=1, actual_cost=0.25, cost_currency="INR",
		)
		frappe.db.commit()

		mixed = gateway.spend(days=1)
		_assert(
			mixed["total_cost"] == 0.25,
			f"A window with one priced row reported a total of {mixed['total_cost']}, "
			f"so a real subtotal was suppressed",
		)
		_assert(
			mixed["total_covers_every_interaction"] is False,
			"A window where only some interactions carry a price reported its total as "
			"covering every interaction",
		)

		# Both assertions above are also satisfied by `not priced` and by a constant
		# False, so neither discriminates on its own. The case that separates them is a
		# window where *every* row is priced: the flag must then be True, which a
		# constant False and `not priced` both get wrong.
		frappe.flags.in_sparsh_maintenance = True
		try:
			frappe.delete_doc("Sparsh Model Interaction", unpriced, force=True,
							  ignore_permissions=True)
		finally:
			frappe.flags.in_sparsh_maintenance = False
		frappe.db.commit()

		complete = gateway.spend(days=1)
		_assert(
			complete["interactions_with_no_cost_recorded"] == 0,
			"The unpriced fixture is still in the window, so the complete case cannot be "
			"tested here",
		)
		_assert(
			complete["total_covers_every_interaction"] is True,
			"A window in which every interaction carries a price still reported its "
			"total as incomplete",
		)

		# The empty-period branch (`bool(rows)` in `nothing_priced`) is deliberately not
		# asserted here: `spend` bounds `days` at 1, and this bench's ledger always holds
		# another check's rows inside any window it will accept. Loosening the bound to
		# make it testable would be changing the code to suit the test.
	finally:
		_delete_all("Sparsh Model Interaction", {"provider": "zzv-nocost"})
		frappe.db.commit()



def check_partial_only_history_is_named_accurately():
	"""A supervisor acts on the reason, so the reason has to be true.

	"Mixed results without an unaided pass" was the catch-all, and it caught histories
	that are not mixed: three Partials gave a supervisor a description of a pattern that
	was not there. A reason nobody can act on is worse than no reason.
	"""
	from sparsh_los import dashboard

	_reset_competency()
	for _ in range(3):
		_new_evidence(ACTIVITY_1, "Partial", assistance_level=1)
	frappe.db.commit()

	rows = [r for r in dashboard.supervisor_view(COMPETENCY)["stuck"] if r["learner"] == LEARNER]
	if not rows:
		# Partial results may not hold a learner in Practising at all; that is a
		# different design question, and this check says so rather than passing silently.
		raise AssertionError(
			f"A learner with three Partial results is not in the stuck list; state is {_state()}"
		)

	for row in rows:
		_assert(
			row["partial_results"] == 3,
			f"The Partial results were not counted: {row.get('partial_results')}",
		)
		_assert(
			row["reason"] != "Mixed results without an unaided pass",
			"A history of only Partial results was described as mixed",
		)
		_assert(
			row["reason"] == "Partial completions only",
			f"A Partial-only history was described as: {row['reason']}",
		)

	_reset_competency()
	frappe.db.commit()



def check_current_resource_material_cannot_change_in_place():
	"""A resource that is Current points at fixed material, or the refresh never fires.

	`on_update` marks readers Refresh Due on `became_current` -- a *transition*. Editing
	the `url` of a resource that is already Current is not a transition, so the edit went
	through, the key was preserved, and every learner who had studied the old material
	kept a competence state asserting mastery of material the row no longer pointed at.

	Refusing the edit, rather than firing a refresh on it, is the point: a refresh with
	no preserved predecessor still loses the record of what was actually studied.
	"""
	resource_id = PREFIX + "MAT"
	_delete_all("Sparsh Learning Resource", {"resource_id": resource_id})
	frappe.db.commit()

	doc = frappe.new_doc("Sparsh Learning Resource")
	doc.resource_id = resource_id
	doc.version = 1
	doc.title = "Material under test"
	doc.resource_type = "Manual section"
	doc.status = "Current"
	doc.url = "https://example.invalid/original"
	doc.insert(ignore_permissions=True)
	frappe.db.commit()

	try:
		# Editorial fields stay editable: refusing these would make an ordinary typo
		# correction require a new version, which is not what was asked for.
		doc = frappe.get_doc("Sparsh Learning Resource", doc.name)
		doc.title = "Material under test, retitled"
		doc.notes = "A clarifying note."
		doc.save(ignore_permissions=True)
		frappe.db.commit()
		_assert(
			frappe.db.get_value("Sparsh Learning Resource", doc.name, "current_key") == resource_id,
			"An editorial edit dropped the current key",
		)

		for field, value in (
			("url", "https://example.invalid/replaced"),
			("lms_lesson", "zzv-some-other-lesson"),
			("file_reference", "zzv-some-other-file"),
			("source", "A different source document"),
			("resource_type", "Explainer"),
		):
			frappe.db.savepoint("sparsh_material")
			try:
				edit = frappe.get_doc("Sparsh Learning Resource", doc.name)
				edit.set(field, value)
				edit.save(ignore_permissions=True)
			except frappe.ValidationError as exc:
				frappe.db.rollback(save_point="sparsh_material")
				_assert(
					"publishing a new version" in str(exc),
					f"Changing {field} was refused, but for another reason: {exc}",
				)
			else:
				frappe.db.rollback(save_point="sparsh_material")
				raise AssertionError(
					f"A Current resource's {field} was changed in place, so learners who "
					f"studied the old material were never marked Refresh Due"
				)
	finally:
		_delete_all("Sparsh Learning Resource", {"resource_id": resource_id})
		frappe.db.commit()


def check_existing_current_resources_are_keyed():
	"""The unique index is only a constraint over rows that carry the key.

	`current_key` arrived with the index. On a site that already held resources, every
	existing row predated the column and held NULL -- and NULLs do not collide, so the
	index installed cleanly over a table that could already contain two Current versions
	of the same resource. The constraint read as enforced while enforcing nothing for
	precisely the rows that were there first.

	The patch backfills them. The first version of this check only asserted the table
	held no unkeyed Current row, which is vacuously true on a bench whose teardown leaves
	the table empty -- deleting the patch failed nothing. So the patch is now run against
	rows built to need it: one unkeyed Current resource, which it must key, and two
	Current versions of one resource, which it must leave unkeyed and report.
	"""
	import contextlib
	import io

	from sparsh_los.patches.v1_0 import backfill_resource_current_keys as patch

	single = PREFIX + "KEYED"
	contested = PREFIX + "CONTESTED"
	_delete_all("Sparsh Learning Resource", {"resource_id": ("in", [single, contested])})
	frappe.db.commit()

	def resource(resource_id, version, status):
		doc = frappe.new_doc("Sparsh Learning Resource")
		doc.resource_id = resource_id
		doc.version = version
		doc.title = "Backfill fixture"
		doc.resource_type = "Manual section"
		doc.status = status
		doc.insert(ignore_permissions=True)
		return doc.name

	def key_of(name):
		return frappe.db.get_value("Sparsh Learning Resource", name, "current_key")

	try:
		# A Current row that predates the column: made by nulling what the controller
		# set, which is exactly the state the patch exists for.
		lone = resource(single, 1, "Current")
		frappe.db.sql(
			"update `tabSparsh Learning Resource` set current_key = null where name = %s", lone
		)
		frappe.db.commit()
		_assert(key_of(lone) is None, "The fixture row still carries a key, so the patch has nothing to do")

		# Two Current versions of one resource. The controller refuses the second on save,
		# so the duplicate is written the way the pre-index data actually arrived: by SQL.
		first = resource(contested, 1, "Current")
		second = resource(contested, 2, "Draft")
		frappe.db.sql(
			"""update `tabSparsh Learning Resource`
			   set current_key = null, status = 'Current' where name in (%s, %s)""",
			(first, second),
		)
		frappe.db.commit()

		report = io.StringIO()
		with contextlib.redirect_stdout(report):
			patch.execute()
		report = report.getvalue()

		_assert(
			key_of(lone) == single,
			f"The patch left the lone Current resource unkeyed: current_key={key_of(lone)!r}",
		)
		_assert(
			key_of(first) is None and key_of(second) is None,
			f"The patch picked a winner between two Current versions: "
			f"{key_of(first)!r}, {key_of(second)!r}",
		)
		_assert(
			f"contested: {contested}" in report and first in report and second in report,
			f"The patch did not report the contested resource and both its rows: {report!r}",
		)

		# And it is idempotent: a second run has nothing to claim and changes nothing.
		with contextlib.redirect_stdout(io.StringIO()):
			patch.execute()
		_assert(key_of(lone) == single, "A second run of the patch disturbed a claimed key")
		_assert(
			key_of(first) is None and key_of(second) is None,
			"A second run of the patch keyed a contested row",
		)
	finally:
		_delete_all("Sparsh Learning Resource", {"resource_id": ("in", [single, contested])})
		frappe.db.commit()

	# What remains on the site after the fixtures are gone must be consistent too: a
	# keyed row keyed to itself, and no key held by a row that is not Current.
	mismatched = frappe.db.sql(
		"""select name, resource_id, current_key from `tabSparsh Learning Resource`
		   where current_key is not null and current_key != '' and current_key != resource_id""",
		as_dict=True,
	)
	_assert(
		not mismatched,
		f"{len(mismatched)} resource(s) hold a key that is not their resource_id",
	)
	stale = frappe.db.sql(
		"""select name from `tabSparsh Learning Resource`
		   where status != 'Current' and current_key is not null and current_key != ''""",
		as_dict=True,
	)
	_assert(
		not stale,
		f"{len(stale)} superseded or draft resource(s) still hold the Current key",
	)

def check_critical_marker_without_rule_blocks_the_pilot():
	"""A critical marker is a permanent block, so it needs a rule behind it too.

	`programme_readiness` looked only at Deterministic activities for the rule gap, so a
	Human-review case carrying `critical_markers` on a competency with no rule linked was
	never named -- and the pilot was called human-review-safe while an irreversible
	safety block could be imposed on the authority of a rule nobody had validated.
	"""
	from sparsh_los import seed

	_delete_all("Sparsh Activity", {"activity_id": OTHER_ACTIVITY})
	borrowed = not frappe.db.exists("Sparsh Competency", OTHER_COMPETENCY)
	if borrowed:
		competency = frappe.new_doc("Sparsh Competency")
		competency.competency_id = OTHER_COMPETENCY
		competency.competency_name = "Verification Competency"
		competency.domain = DOMAIN
		competency.insert(ignore_permissions=True)
	else:
		# `other_domain_runs_unchanged` links a governing rule to this competency now
		# that the gate fails closed. The unruled competency is this check's subject,
		# so the link has to come off before the fixture means anything.
		_unlink_rules(OTHER_COMPETENCY)
	frappe.db.commit()
	_assert(
		not frappe.get_all("Sparsh Competency Rule Link", filters={"parent": OTHER_COMPETENCY}, limit=1),
		"The fixture competency has a rule linked, so this proves nothing",
	)

	activity = frappe.new_doc("Sparsh Activity")
	activity.activity_id = OTHER_ACTIVITY
	activity.title = "Verification Activity"
	activity.competency = OTHER_COMPETENCY
	activity.activity_type = "Short case"
	activity.instruction = "Verification instruction."
	activity.version = 1
	# Human review, deliberately: the mode the Deterministic-only loop never saw.
	activity.evaluation_mode = "Human review"
	activity.critical_markers = "zzv unsafe marker"
	activity.insert(ignore_permissions=True)
	frappe.db.commit()

	# Ambient blockers are muted exactly as `no_rule_auto_scoring_blocks_the_pilot`
	# mutes them, so the verdict below can be attributed to this fixture and nothing else.
	muted, stand_ins = [], []
	baseline = seed.programme_readiness()
	for name in baseline["auto_scoring_with_no_rule_linked"]:
		activity_id = frappe.db.get_value("Sparsh Activity", name, "activity_id") or ""
		_assert(
			activity_id.startswith(PREFIX),
			f"{name} auto-scores with no rule linked, so the verdict is False regardless "
			"and this check cannot discriminate",
		)
		muted.append(name)
		frappe.db.set_value("Sparsh Activity", name, "evaluation_mode", "Human review")
	# The harness's own activities carry markers from `runner_loop` and link no rule,
	# so they are in this list too; cleared for the duration and restored in the finally.
	unmarked = {}
	for name in baseline["critical_markers_with_no_rule_linked"]:
		if name == activity.name:
			continue
		activity_id = frappe.db.get_value("Sparsh Activity", name, "activity_id") or ""
		_assert(
			activity_id.startswith(PREFIX),
			f"{name} already carries a critical marker with no rule linked, so the verdict "
			"is False regardless and this check cannot discriminate",
		)
		unmarked[name] = frappe.db.get_value("Sparsh Activity", name, "critical_markers")
		frappe.db.set_value("Sparsh Activity", name, "critical_markers", None)
	for competency_id in baseline["competencies_without_activities"]:
		_assert(
			competency_id.startswith(PREFIX),
			f"{competency_id} has no activity, so the verdict is False regardless",
		)
		stand_in = frappe.new_doc("Sparsh Activity")
		stand_in.activity_id = PREFIX + "STANDIN-" + competency_id
		stand_in.title = "Verification Activity"
		stand_in.competency = competency_id
		stand_in.activity_type = "Knowledge check"
		stand_in.instruction = "Verification instruction."
		stand_in.version = 1
		stand_in.evaluation_mode = "Human review"
		stand_in.insert(ignore_permissions=True)
		stand_ins.append(stand_in.activity_id)
	frappe.db.commit()

	try:
		readiness = seed.programme_readiness()
		_assert(
			activity.name in readiness["critical_markers_with_no_rule_linked"],
			"A Human-review activity with critical markers and no rule was not reported: "
			f"{readiness['critical_markers_with_no_rule_linked']}",
		)
		_assert(
			readiness["can_pilot_with_human_review"] is False,
			"Readiness called the pilot human-review-safe while an activity could impose a "
			"critical block under no validated rule",
		)

		# The marker has to be shown to be the cause: with it cleared, and nothing else
		# changed, the verdict must turn.
		frappe.db.set_value("Sparsh Activity", activity.name, "critical_markers", None)
		frappe.db.commit()
		without = seed.programme_readiness()
		_assert(
			activity.name not in without["critical_markers_with_no_rule_linked"],
			"An activity with no critical marker was still reported as carrying one",
		)
		_assert(
			without["can_pilot_with_human_review"],
			"The pilot is blocked by something other than the fixture, so this check "
			f"cannot discriminate: gaps={without['competencies_without_activities']} "
			f"unvalidated={without['auto_scoring_against_unvalidated_rules']} "
			f"no_rule={without['auto_scoring_with_no_rule_linked']} "
			f"critical={without['critical_markers_with_no_rule_linked']}",
		)
	finally:
		for name in muted:
			frappe.db.set_value("Sparsh Activity", name, "evaluation_mode", "Deterministic")
		for name, markers in unmarked.items():
			frappe.db.set_value("Sparsh Activity", name, "critical_markers", markers)
		_delete_all("Sparsh Activity", {"activity_id": OTHER_ACTIVITY})
		for activity_id in stand_ins:
			_delete_all("Sparsh Activity", {"activity_id": activity_id})
		if borrowed:
			_delete_all("Sparsh Competency", {"competency_id": OTHER_COMPETENCY})
		frappe.db.commit()


def check_nobody_closes_their_own_refresher():
	"""A refresher is closed by fresh work, never by the person it was assigned to.

	A learner who also holds the reviewer role carries write on the assignment, so they
	could set their own row to Completed -- or delete it -- and have the engine restore
	their suspended certificate on their own say-so. The engine's own path,
	`refresher.close_satisfied`, writes with `db.set_value` and must keep working.
	"""
	from sparsh_los import refresher

	dual = DUAL_LEARNER
	user = _make_learner(dual)
	if "Sparsh Reviewer" not in [r.role for r in user.roles]:
		user.append("roles", {"role": "Sparsh Reviewer"})
		user.save(ignore_permissions=True)
	_reset_competency()
	_delete_all("Sparsh Refresher Assignment", {"learner": dual})
	frappe.db.commit()

	assignment = refresher.assign(dual, COMPETENCY, refresher.PERFORMANCE_GAP, "verification")
	_assert(assignment, "The fixture refresher was not assigned")
	frappe.db.commit()

	original_user = frappe.session.user
	try:
		frappe.set_user(dual)

		def close_own():
			doc = frappe.get_doc("Sparsh Refresher Assignment", assignment)
			doc.status = "Completed"
			doc.save()

		_refused(
			close_own,
			"A learner-reviewer closed their own refresher",
			expect="cannot close a refresher assigned to you",
		)

		# `ignore_permissions=True`, deliberately: the DocPerm layer refuses an ordinary
		# delete from this role before `on_trash` runs, with no message, so the plain path
		# would pass on the framework and never reach the guard under test. The guard is
		# for the paths that skip DocPerm -- a System Manager who is also the learner,
		# or engine code deleting with permissions ignored.
		_refused(
			lambda: frappe.delete_doc(
				"Sparsh Refresher Assignment", assignment, ignore_permissions=True
			),
			"A learner-reviewer deleted their own refresher",
			expect="cannot delete a refresher assigned to you",
		)
	finally:
		frappe.set_user(original_user)

	_assert(
		frappe.db.get_value("Sparsh Refresher Assignment", assignment, "status") == "Assigned",
		"The refused closure went through anyway",
	)

	# The guard must not reach the engine: an independent pass filed after the
	# assignment closes it through `close_satisfied`, inside `recompute_mastery`.
	_new_evidence(ACTIVITY_1, "Pass", learner=dual)
	frappe.db.commit()
	_assert(
		frappe.db.get_value("Sparsh Refresher Assignment", assignment, "status") == "Completed",
		"Fresh evidence no longer closes a refresher: the self-closure guard broke the engine",
	)

	_delete_all("Sparsh Refresher Assignment", {"learner": dual})
	frappe.db.commit()


def check_repeated_failures_reach_the_supervisor_from_attempts():
	"""A learner who has never passed has no Evidence, and must still be seen.

	Every earlier stuck check inserted Evidence directly. The runner writes Evidence on a
	pass or a critical error and on nothing else, so a learner who answers wrongly three
	times produces three Attempts, no Evidence and no Mastery State row -- and was the one
	learner the supervisor view could not show. Built through `runner.submit` so the real
	failure path is what is exercised.
	"""
	from sparsh_los import dashboard

	_reset_competency()
	_make_learner(TEST_LEARNER)
	_delete_all("Sparsh Attempt", {"learner": TEST_LEARNER})
	_delete_all("Sparsh Evidence", {"learner": TEST_LEARNER, "competency": COMPETENCY})
	_delete_mastery({"learner": TEST_LEARNER, "competency": COMPETENCY})
	frappe.db.commit()

	activity = frappe.get_doc("Sparsh Activity", ACTIVITY_1)
	original_mode, original_expected = activity.evaluation_mode, activity.expected_response
	activity.evaluation_mode = "Deterministic"
	activity.expected_response = "level two"
	activity.save(ignore_permissions=True)
	frappe.db.commit()

	def stuck_row():
		view = dashboard.supervisor_view(competency=COMPETENCY)
		return next((r for r in view["stuck"] if r["learner"] == TEST_LEARNER), None)

	try:
		for _ in range(dashboard.FAILING_ATTEMPTS_BEFORE_STUCK - 1):
			result = _submit(ACTIVITY_1, "zzv wrong answer", as_user=TEST_LEARNER)
			_assert(result["outcome"] == "Fail", f"A wrong answer scored {result['outcome']}")
			_assert("evidence" not in result, "A failed attempt produced evidence")
		frappe.db.commit()
		_assert(
			stuck_row() is None,
			"A learner short of the failure threshold was already shown as stuck",
		)

		result = _submit(ACTIVITY_1, "zzv wrong answer", as_user=TEST_LEARNER)
		_assert(result["outcome"] == "Fail", f"A wrong answer scored {result['outcome']}")
		frappe.db.commit()

		# The fixture has to be what it claims: attempts only.
		_assert(
			not frappe.db.exists("Sparsh Evidence", {"learner": TEST_LEARNER, "competency": COMPETENCY}),
			"The failing learner has Evidence, so this is not the attempt-only path",
		)
		_assert(
			not frappe.db.exists("Sparsh Mastery State", {"learner": TEST_LEARNER, "competency": COMPETENCY}),
			"The failing learner has a Mastery State row, so this is not the attempt-only path",
		)

		row = stuck_row()
		_assert(
			row,
			f"A learner with {dashboard.FAILING_ATTEMPTS_BEFORE_STUCK} failing attempts and "
			"no evidence is absent from the stuck list",
		)
		_assert(
			row["reason"] == "Repeated failures",
			f"The reason was {row['reason']!r}, expected 'Repeated failures'",
		)
		_assert(
			row["failures"] == dashboard.FAILING_ATTEMPTS_BEFORE_STUCK,
			f"{row['failures']} failures counted, expected {dashboard.FAILING_ATTEMPTS_BEFORE_STUCK}",
		)
	finally:
		activity.reload()
		activity.evaluation_mode = original_mode
		activity.expected_response = original_expected
		activity.save(ignore_permissions=True)
		_delete_all("Sparsh Attempt", {"learner": TEST_LEARNER})
		frappe.db.commit()


def check_review_verdict_lands_on_the_evidence():
	"""A rejection the engine never sees is not a rejection.

	Four modules exclude evidence whose `human_review_status` is Rejected, and until the
	review controller wrote that value nothing in the engine ever did: a reviewer could
	reject a pass and watch it keep counting. Asserted as a delta on the derived state.
	"""
	_reset_competency()
	evidence = _new_evidence(ACTIVITY_1, "Pass")
	frappe.db.commit()
	_assert(_state() == DEMONSTRATED, f"The fixture pass gave {_state()}, expected Demonstrated")
	_assert(
		frappe.db.get_value("Sparsh Evidence", evidence.name, "human_review_status") != "Rejected",
		"The fixture evidence is already rejected, so the delta cannot be measured",
	)

	review = frappe.new_doc("Sparsh Human Review")
	review.evidence = evidence.name
	review.review_status = "Rejected"
	review.reviewer_comments = "Verification rejection."
	review.insert(ignore_permissions=True)
	review.submit()
	frappe.db.commit()

	_assert(
		frappe.db.get_value("Sparsh Evidence", evidence.name, "human_review_status") == "Rejected",
		"A submitted rejection did not reach the evidence it examined",
	)
	_assert(
		_state() not in (DEMONSTRATED, MASTERED),
		f"The rejected pass still counts: the state stayed at {_state()}",
	)
	frappe.db.commit()


def check_awaiting_a_person_agrees_with_the_queue():
	"""The summary's 'awaiting a person' figure describes the reviewer's queue.

	An attempt is immutable, so a reviewed one keeps `Not Evaluated` for ever; counting
	that outcome grew without bound and never agreed with the queue it claimed to
	describe. Both figures are taken from the same bench at the same moment, and the
	summary is also asserted to fall by exactly the one attempt that was reviewed.
	"""
	from sparsh_los import dashboard, review

	_reset_competency()
	_make_learner(TEST_LEARNER)
	_delete_all("Sparsh Attempt", {"learner": TEST_LEARNER})
	frappe.db.commit()

	activity = frappe.get_doc("Sparsh Activity", ACTIVITY_2)
	original_mode = activity.evaluation_mode
	activity.evaluation_mode = "Human review"
	activity.save(ignore_permissions=True)
	frappe.db.commit()

	try:
		first = _submit(ACTIVITY_2, "zzv first answer for a person", as_user=TEST_LEARNER)
		second = _submit(ACTIVITY_2, "zzv second answer for a person", as_user=TEST_LEARNER)
		frappe.db.commit()
		for result in (first, second):
			_assert(result["outcome"] == "Not Evaluated", f"A reviewed activity scored {result['outcome']}")

		waiting = {w["name"] for w in review.pending(limit=review.MAX_QUEUE)}
		_assert(
			first["attempt"] in waiting and second["attempt"] in waiting,
			"The fixture attempts are not both in the queue, so the comparison is meaningless",
		)
		_assert(
			len(waiting) < review.MAX_QUEUE,
			"The queue is at its cap, so its length cannot be compared to a count",
		)
		before = dashboard.programme_summary()["attempts_awaiting_a_person"]
		_assert(
			before == len(waiting),
			f"The summary says {before} attempts await a person; the queue holds {len(waiting)}",
		)

		review.record_evidence(first["attempt"], "Pass", assistance_level=0)
		frappe.db.commit()

		waiting_after = {w["name"] for w in review.pending(limit=review.MAX_QUEUE)}
		_assert(first["attempt"] not in waiting_after, "A reviewed attempt is still queued")
		_assert(second["attempt"] in waiting_after, "The unreviewed attempt left the queue")
		after = dashboard.programme_summary()["attempts_awaiting_a_person"]
		_assert(
			after == before - 1,
			f"Reviewing one attempt moved the summary from {before} to {after}, expected {before - 1}",
		)
		_assert(
			after == len(waiting_after),
			f"The summary says {after} attempts await a person; the queue holds {len(waiting_after)}",
		)
	finally:
		activity.reload()
		activity.evaluation_mode = original_mode
		activity.save(ignore_permissions=True)
		frappe.db.commit()


def check_learner_cannot_read_rules_or_competencies():
	"""The rule inventory and the competency's observable behaviours are not for learners.

	All the matrix rules are unvalidated Drafts, and `observable_behaviours` becomes an
	answer key the moment Rubric scoring exists. Asserted through paths that apply
	permissions -- `frappe.has_permission` and `frappe.client.get_list` -- because
	`frappe.get_all` and `frappe.get_doc` check nothing and once produced eleven false
	ALLOWED results. The learner's read on `Sparsh Learning Resource` is deliberately
	kept: it is their only route to the material they are meant to study, and it is
	asserted to still work so this check cannot be read as "less access is always better".
	"""
	_make_learner(TEST_LEARNER)
	resource_id = PREFIX + "READABLE"
	_delete_all("Sparsh Learning Resource", {"resource_id": resource_id})
	resource = frappe.new_doc("Sparsh Learning Resource")
	resource.resource_id = resource_id
	resource.version = 1
	resource.title = "Material a learner may read"
	resource.resource_type = "Manual section"
	resource.status = "Current"
	resource.insert(ignore_permissions=True)
	frappe.db.commit()

	original_user = frappe.session.user
	try:
		frappe.set_user(TEST_LEARNER)
		for doctype in ("Sparsh Source of Truth Rule", "Sparsh Competency"):
			_assert(
				not frappe.has_permission(doctype, "read", user=TEST_LEARNER),
				f"A learner holds read on {doctype}",
			)
			_refused(
				lambda: frappe.client.get_list(doctype, fields=["name"], limit_page_length=1),
				f"A learner could list {doctype} rows",
				expect="insufficient permission",
			)

		_assert(
			frappe.has_permission("Sparsh Learning Resource", "read", user=TEST_LEARNER),
			"A learner lost read on Sparsh Learning Resource, their only route to the material",
		)
		visible = frappe.client.get_list(
			"Sparsh Learning Resource", filters={"resource_id": resource_id}, fields=["name"]
		)
		_assert(
			any(r["name"] == resource.name for r in visible),
			"A learner cannot see a Current learning resource",
		)
	finally:
		frappe.set_user(original_user)
		_delete_all("Sparsh Learning Resource", {"resource_id": resource_id})
		frappe.db.commit()


def check_practice_page_records_the_session():
	"""Opening the page is the session, and offering an activity is starting it.

	`activity_started` used to fire only from the harness: the page read title and
	instruction straight off the Activity, so the frequency-of-use metric read zero in
	real use. `events_are_recorded_and_hold_no_content` drives `runner.start` itself and
	would pass with the page reverted, so this goes through `practice.get_context`.
	"""
	from sparsh_los import events
	from sparsh_los.www import practice

	_reset_competency()
	_new_evidence(ACTIVITY_1, "Pass")
	frappe.db.commit()

	started_at = frappe.utils.now_datetime()
	context = frappe._dict()
	original_user = frappe.session.user
	try:
		frappe.set_user(LEARNER)
		practice.get_context(context)
	finally:
		frappe.set_user(original_user)
	frappe.db.commit()

	_assert(context.next_up and context.next_up.get("activity"), "The page offered nothing, so no activity could start")
	offered = context.next_up["activity"]
	_assert(context.next_up.get("title"), "next_up carries no title")
	_assert(context.next_up.get("instruction"), "next_up carries no instruction")

	rows = frappe.get_all(
		"Sparsh Event",
		filters={"learner": LEARNER, "creation": (">=", started_at)},
		fields=["event_type", "activity"],
	)
	_assert(
		any(r.event_type == events.SESSION_STARTED for r in rows),
		f"Rendering the practice page recorded no {events.SESSION_STARTED}; saw {sorted({r.event_type for r in rows})}",
	)
	_assert(
		any(r.event_type == events.ACTIVITY_STARTED and r.activity == offered for r in rows),
		f"Offering {offered} on the practice page recorded no {events.ACTIVITY_STARTED} for it; "
		f"saw {sorted({r.event_type for r in rows})}",
	)


# ------------------------------------------------------------ phase 1: cohorts
COHORT_A = PREFIX + "COH1"
COHORT_B = PREFIX + "COH2"
REVIEWER_ONLY = "zzv-reviewer@example.invalid"
COHORT_ACTIVITY = PREFIX + "ACT-C1"


def _make_reviewer(email):
	"""A reviewer holding no learner role, for asking what the reviewer role alone grants."""
	if frappe.db.exists("User", email):
		return frappe.get_doc("User", email)
	user = frappe.new_doc("User")
	user.email = email
	user.first_name = "Verification"
	user.enabled = 1
	user.user_type = "System User"
	user.append("roles", {"role": "Sparsh Reviewer"})
	user.insert(ignore_permissions=True)
	return user


def _clear_cohorts():
	_delete_all("Sparsh Cohort", {"name": ("like", PREFIX + "%")})
	frappe.db.commit()


def _new_cohort(cohort_id, learners, status="Draft", pathway=None, joined_on=None):
	doc = frappe.new_doc("Sparsh Cohort")
	doc.cohort_id = cohort_id
	doc.title = "Verification cohort"
	doc.status = status
	doc.pathway = pathway
	for learner in learners:
		row = {"learner": learner}
		if joined_on:
			row["joined_on"] = joined_on
		doc.append("members", row)
	doc.insert(ignore_permissions=True)
	return doc


def _member_keys(cohort):
	return {
		r.learner: r.active_key
		for r in frappe.get_all(
			"Sparsh Cohort Member",
			filters={"parent": cohort, "parenttype": "Sparsh Cohort"},
			fields=["learner", "active_key"],
		)
	}


def check_cohort_permission_model():
	"""Learners hold nothing on Sparsh Cohort; reviewers manage it but cannot delete it.

	Asked through `frappe.has_permission`, which applies the DocPerm rows, and not
	through the rows alone: a DocPerm that exists but is ignored would pass a row scan.
	"""
	_make_learner(TEST_LEARNER)
	_make_reviewer(REVIEWER_ONLY)
	frappe.db.commit()

	learner_rows = frappe.get_all(
		"DocPerm", filters={"parent": "Sparsh Cohort", "role": "Sparsh Learner"}, pluck="name"
	)
	_assert(not learner_rows, f"Sparsh Learner holds {len(learner_rows)} DocPerm row(s) on Sparsh Cohort")
	for ptype in ("read", "write", "create", "delete"):
		_assert(
			not frappe.has_permission("Sparsh Cohort", ptype, user=TEST_LEARNER),
			f"A learner-only account has {ptype} on Sparsh Cohort",
		)

	reviewer = frappe.db.get_value(
		"DocPerm",
		{"parent": "Sparsh Cohort", "role": "Sparsh Reviewer"},
		["`read`", "`write`", "`create`", "`delete`"],
		as_dict=True,
	)
	_assert(reviewer, "Sparsh Reviewer has no DocPerm row on Sparsh Cohort")
	_assert(
		reviewer.read == 1 and reviewer.write == 1 and reviewer.create == 1,
		f"Reviewer DocPerm on Sparsh Cohort is read={reviewer.read} write={reviewer.write} create={reviewer.create}",
	)
	_assert(reviewer.delete == 0, "Sparsh Reviewer can delete a cohort")
	for ptype in ("read", "write", "create"):
		_assert(
			frappe.has_permission("Sparsh Cohort", ptype, user=REVIEWER_ONLY),
			f"A reviewer-only account lacks {ptype} on Sparsh Cohort",
		)
	_assert(
		not frappe.has_permission("Sparsh Cohort", "delete", user=REVIEWER_ONLY),
		"A reviewer-only account can delete a cohort",
	)


def check_cohort_member_indexes_exist():
	"""Both cohort-member uniqueness rules are indexes, over the columns they claim."""
	rows = frappe.db.sql("show index from `tabSparsh Cohort Member`", as_dict=1)
	unique = {}
	for r in rows:
		if r["Non_unique"] == 0:
			unique.setdefault(r["Key_name"], []).append((r["Seq_in_index"], r["Column_name"]))
	columns = {k: [c for _seq, c in sorted(v)] for k, v in unique.items()}

	_assert(
		"unique_active_cohort_member" in columns,
		f"No unique index unique_active_cohort_member; unique indexes are {sorted(columns)}",
	)
	_assert(
		columns["unique_active_cohort_member"] == ["active_key"],
		f"unique_active_cohort_member covers {columns['unique_active_cohort_member']}, not [active_key]",
	)
	_assert(
		"unique_cohort_learner" in columns,
		f"No unique index unique_cohort_learner; unique indexes are {sorted(columns)}",
	)
	_assert(
		columns["unique_cohort_learner"] == ["parent", "learner"],
		f"unique_cohort_learner covers {columns['unique_cohort_learner']}, not [parent, learner]",
	)


def check_cohort_joined_on_is_server_set():
	"""`joined_on` is the server's clock on insert and frozen thereafter, whatever the payload says."""
	_make_learner(TEST_LEARNER)
	_clear_cohorts()
	try:
		before = frappe.utils.now_datetime()
		doc = _new_cohort(COHORT_A, [TEST_LEARNER], joined_on="2001-01-01 00:00:00")
		frappe.db.commit()
		stored = frappe.db.get_value(
			"Sparsh Cohort Member", {"parent": COHORT_A, "learner": TEST_LEARNER}, "joined_on"
		)
		_assert(stored is not None, "joined_on was not set at all")
		_assert(
			frappe.utils.get_datetime(stored).year != 2001,
			f"The payload's joined_on was stored: {stored}",
		)
		_assert(
			abs((frappe.utils.get_datetime(stored) - before).total_seconds()) < 120,
			f"joined_on {stored} is not the server clock at insert ({before})",
		)

		doc.reload()
		doc.title = "Verification cohort, edited"
		doc.members[0].joined_on = "2001-01-01 00:00:00"
		doc.save(ignore_permissions=True)
		frappe.db.commit()
		after = frappe.db.get_value(
			"Sparsh Cohort Member", {"parent": COHORT_A, "learner": TEST_LEARNER}, "joined_on"
		)
		_assert(
			frappe.utils.get_datetime(after) == frappe.utils.get_datetime(stored),
			f"An edit changed joined_on from {stored} to {after}",
		)
	finally:
		_clear_cohorts()


def check_cohort_refuses_duplicate_member():
	_make_learner(TEST_LEARNER)
	_clear_cohorts()
	try:
		_raises(
			lambda: _new_cohort(COHORT_A, [TEST_LEARNER, TEST_LEARNER]),
			"A learner listed twice in one cohort was accepted",
			expect="listed twice",
		)
		_assert(not frappe.db.exists("Sparsh Cohort", COHORT_A), "The refused cohort was stored")
	finally:
		_clear_cohorts()


def check_one_active_cohort_per_learner():
	"""A learner follows one pathway at a time; Draft membership elsewhere is fine."""
	from sparsh_los import cohort

	_make_learner(TEST_LEARNER)
	_clear_cohorts()
	try:
		_new_cohort(COHORT_A, [TEST_LEARNER], status="Active")
		frappe.db.commit()

		_raises(
			lambda: _new_cohort(COHORT_B, [TEST_LEARNER], status="Active"),
			"A second Active cohort took a learner already in an Active one",
			expect="already belongs",
		)

		second = _new_cohort(COHORT_B, [TEST_LEARNER], status="Draft")
		frappe.db.commit()
		_assert(cohort.cohort_for(TEST_LEARNER) == COHORT_A, "The Draft cohort displaced the Active one")

		first = frappe.get_doc("Sparsh Cohort", COHORT_A)
		first.status = "Closed"
		first.save(ignore_permissions=True)
		second.reload()
		second.status = "Active"
		second.save(ignore_permissions=True)
		frappe.db.commit()
		_assert(
			cohort.cohort_for(TEST_LEARNER) == COHORT_B,
			f"After closing the first cohort, cohort_for returned {cohort.cohort_for(TEST_LEARNER)!r}",
		)
	finally:
		_clear_cohorts()


def check_active_key_is_enforced_below_validate():
	"""The unique index, not the controller query, holds the one-Active-cohort rule.

	A raw insert carries the key past `validate` entirely; only the database can refuse it.
	"""
	_make_learner(TEST_LEARNER)
	_clear_cohorts()
	try:
		_new_cohort(COHORT_A, [TEST_LEARNER], status="Active")
		_new_cohort(COHORT_B, [], status="Active")
		frappe.db.commit()

		raised = None
		try:
			frappe.db.sql(
				"""insert into `tabSparsh Cohort Member`
				   (name, creation, modified, modified_by, owner, docstatus, idx,
				    parent, parentfield, parenttype, learner, active_key)
				   values (%(name)s, now(), now(), 'Administrator', 'Administrator', 0, 1,
				    %(parent)s, 'members', 'Sparsh Cohort', %(learner)s, %(learner)s)""",
				{"name": PREFIX + "rawmember", "parent": COHORT_B, "learner": TEST_LEARNER},
			)
		except Exception as exc:  # noqa: BLE001
			raised = exc
		frappe.db.rollback()

		_assert(raised is not None, "A raw insert of a second active_key for one learner was accepted")
		_assert(
			isinstance(raised, frappe.UniqueValidationError)
			or "1062" in str(raised)
			or "IntegrityError" in type(raised).__name__,
			f"The raw insert failed, but not on the unique index: {type(raised).__name__}: {raised}",
		)
		_assert(
			frappe.db.count("Sparsh Cohort Member", {"active_key": TEST_LEARNER}) == 1,
			"More than one row holds the learner's active_key",
		)
	finally:
		_clear_cohorts()


def check_active_key_survives_ordinary_edit():
	"""The key is derived on every save, so an edit or an append to an Active cohort keeps the index armed."""
	_make_learner(TEST_LEARNER)
	_make_learner(OTHER_LEARNER)
	_clear_cohorts()
	try:
		doc = _new_cohort(COHORT_A, [TEST_LEARNER], status="Active")
		frappe.db.commit()
		_assert(
			_member_keys(COHORT_A) == {TEST_LEARNER: TEST_LEARNER},
			f"Activation did not key the member: {_member_keys(COHORT_A)}",
		)

		doc.reload()
		doc.title = "Verification cohort, retitled"
		doc.save(ignore_permissions=True)
		frappe.db.commit()
		_assert(
			_member_keys(COHORT_A) == {TEST_LEARNER: TEST_LEARNER},
			f"A title edit changed the member's active_key: {_member_keys(COHORT_A)}",
		)

		doc.reload()
		doc.append("members", {"learner": OTHER_LEARNER})
		doc.save(ignore_permissions=True)
		frappe.db.commit()
		_assert(
			_member_keys(COHORT_A) == {TEST_LEARNER: TEST_LEARNER, OTHER_LEARNER: OTHER_LEARNER},
			f"A member appended to an already-Active cohort was not keyed: {_member_keys(COHORT_A)}",
		)

		doc.reload()
		doc.status = "Closed"
		doc.save(ignore_permissions=True)
		frappe.db.commit()
		_assert(
			_member_keys(COHORT_A) == {TEST_LEARNER: None, OTHER_LEARNER: None},
			f"Closing did not release the keys: {_member_keys(COHORT_A)}",
		)
	finally:
		_clear_cohorts()


def check_pathway_for_refuses_ambiguity():
	"""Two Active cohorts for one learner, reached by the bypass path, are refused by name -- not resolved."""
	from sparsh_los import cohort

	_make_learner(TEST_LEARNER)
	_clear_cohorts()
	try:
		_new_cohort(COHORT_A, [TEST_LEARNER], status="Draft")
		_new_cohort(COHORT_B, [TEST_LEARNER], status="Draft")
		frappe.db.commit()
		# `db.set_value` skips validate and the key, so both can be Active at once.
		frappe.db.set_value("Sparsh Cohort", COHORT_A, "status", "Active")
		frappe.db.set_value("Sparsh Cohort", COHORT_B, "status", "Active")
		frappe.db.commit()

		message = None
		try:
			cohort.pathway_for(TEST_LEARNER)
		except frappe.ValidationError as exc:
			message = str(exc)
		_assert(message is not None, "pathway_for picked a pathway for a learner in two Active cohorts")
		_assert(
			COHORT_A in message and COHORT_B in message,
			f"The refusal does not name both cohorts: {message}",
		)
		_assert("more than one active cohort" in message.lower(), f"Refused for another reason: {message}")
	finally:
		_clear_cohorts()


def check_cohort_members_is_a_reviewer_action():
	from sparsh_los import cohort

	_make_learner(TEST_LEARNER)
	_clear_cohorts()
	try:
		_new_cohort(COHORT_A, [TEST_LEARNER], status="Active")
		frappe.db.commit()

		original_user = frappe.session.user
		try:
			frappe.set_user(TEST_LEARNER)
			_refused(
				lambda: cohort.members(COHORT_A),
				"A learner read a cohort's membership list",
				expect="reviewer action",
			)
		finally:
			frappe.set_user(original_user)

		rows = cohort.members(COHORT_A)
		_assert(
			[r.learner for r in rows] == [TEST_LEARNER],
			f"The reviewer's membership list is wrong: {[r.learner for r in rows]}",
		)
		_assert(rows[0].joined_on, "The membership list carries no join date")
	finally:
		_clear_cohorts()


def check_cohort_readiness_restricts_to_cohort():
	"""`cohort=` narrows readiness to that cohort's members; without it, everyone with a state."""
	from sparsh_los import certification

	_make_learner(TEST_LEARNER)
	_make_learner(OTHER_LEARNER)
	_reset_competency()
	_clear_cohorts()
	try:
		_new_evidence(ACTIVITY_1, "Pass", learner=TEST_LEARNER)
		_new_evidence(ACTIVITY_1, "Pass", learner=OTHER_LEARNER)
		_new_cohort(COHORT_A, [TEST_LEARNER], status="Active")
		frappe.db.commit()

		def named(report):
			return {row["learner"] for bucket in report["buckets"].values() for row in bucket}

		scoped = certification.cohort_readiness(COMPETENCY, cohort=COHORT_A)
		_assert(scoped["cohort"] == COHORT_A, "The report does not say which cohort it describes")
		_assert(TEST_LEARNER in named(scoped), "The cohort member is missing from the scoped report")
		_assert(OTHER_LEARNER not in named(scoped), "A non-member appears in the cohort-scoped report")

		everyone = certification.cohort_readiness(COMPETENCY)
		_assert(
			TEST_LEARNER in named(everyone) and OTHER_LEARNER in named(everyone),
			f"The unscoped report is missing a learner with a state: {sorted(named(everyone))}",
		)
	finally:
		_clear_cohorts()
		_reset_competency()


# ---------------------------------------------------- phase 1: reflection, urgency
def _set_mode(activity_name, mode, critical_markers=None):
	activity = frappe.get_doc("Sparsh Activity", activity_name)
	original = (activity.evaluation_mode, activity.critical_markers)
	activity.evaluation_mode = mode
	if critical_markers is not None:
		activity.critical_markers = critical_markers
	activity.save(ignore_permissions=True)
	frappe.db.commit()
	return original


def _restore_mode(activity_name, original):
	activity = frappe.get_doc("Sparsh Activity", activity_name)
	activity.evaluation_mode, activity.critical_markers = original
	activity.save(ignore_permissions=True)
	frappe.db.commit()


def check_reflection_is_not_queued():
	"""A reflection is stored for the learner and waits for nobody."""
	from sparsh_los import review

	_make_learner(TEST_LEARNER)
	_reset_competency()
	original = _set_mode(ACTIVITY_2, "Reflection")
	try:
		state_before = _state(TEST_LEARNER)
		result = _submit(ACTIVITY_2, "zzv what I learned from this case", as_user=TEST_LEARNER)
		frappe.db.commit()

		_assert(result["outcome"] == "Not Evaluated", f"A reflection was scored {result['outcome']}")
		_assert(
			frappe.db.get_value("Sparsh Attempt", result["attempt"], "outcome") == "Not Evaluated",
			"The stored reflection attempt is not Not Evaluated",
		)
		_assert("not scored" in result["message"].lower(), f"The message promised something else: {result['message']}")
		_assert(
			not frappe.db.exists("Sparsh Evidence", {"attempt": result["attempt"]}),
			"A reflection produced Evidence",
		)
		_assert(_state(TEST_LEARNER) == state_before, "A reflection moved mastery")
		waiting = {w["name"] for w in review.pending(limit=review.MAX_QUEUE)}
		_assert(result["attempt"] not in waiting, "A reflection sits in the reviewer's queue")

		# Positive control: the same activity in Human review mode is queued.
		_set_mode(ACTIVITY_2, "Human review")
		control = _submit(ACTIVITY_2, "zzv an answer for a person", as_user=TEST_LEARNER)
		frappe.db.commit()
		waiting = {w["name"] for w in review.pending(limit=review.MAX_QUEUE)}
		_assert(
			control["attempt"] in waiting,
			"The Human review control is not queued, so the reflection's absence proves nothing",
		)
	finally:
		_restore_mode(ACTIVITY_2, original)


def check_reflection_cannot_become_evidence():
	from sparsh_los import review

	_make_learner(TEST_LEARNER)
	_reset_competency()
	original = _set_mode(ACTIVITY_2, "Reflection")
	try:
		result = _submit(ACTIVITY_2, "zzv a private reflection", as_user=TEST_LEARNER)
		frappe.db.commit()
		_raises(
			lambda: review.record_evidence(result["attempt"], "Pass", assistance_level=0),
			"A reviewer turned a reflection into Evidence",
			expect="not turned into evidence",
		)
		_assert(
			not frappe.db.exists("Sparsh Evidence", {"attempt": result["attempt"]}),
			"Evidence exists for the reflection attempt",
		)
	finally:
		_restore_mode(ACTIVITY_2, original)


def check_critical_reflection_escalates_without_evidence():
	"""A reflection matching a critical marker reaches a person at once, and writes no Evidence.

	Under a Validated rule -- so the rule gate is not what stops the score -- the
	reflection path must still come before the safety branch: critical Evidence is
	permanent and a learner's own writing is not a demonstration.
	"""
	_make_learner(TEST_LEARNER)
	_reset_competency()
	_delete_all("Sparsh Escalation Question", {"learner": TEST_LEARNER})
	rule = _new_rule(1, status="Validated")
	competency = frappe.get_doc("Sparsh Competency", COMPETENCY)
	competency.set("linked_rules", [])
	competency.append("linked_rules", {"rule": rule.name})
	competency.save(ignore_permissions=True)
	original = _set_mode(ACTIVITY_2, "Reflection", critical_markers="stop the medicine")
	try:
		result = _submit(ACTIVITY_2, "I would stop the medicine", as_user=TEST_LEARNER)
		frappe.db.commit()

		_assert(result["outcome"] == "Not Evaluated", f"A critical reflection was scored {result['outcome']}")
		_assert(result["critical_error"] == 0, "A critical reflection recorded a critical error on the attempt")
		_assert(
			not frappe.db.exists("Sparsh Evidence", {"attempt": result["attempt"]}),
			"A critical reflection wrote Evidence",
		)
		_assert(result.get("escalation"), "A critical reflection raised no escalation")
		question = frappe.get_doc("Sparsh Escalation Question", result["escalation"])
		_assert(question.attempt == result["attempt"], "The escalation does not cite the attempt")
		_assert(question.escalation_reason == "Safety critical", f"Reason is {question.escalation_reason}")
		_assert(question.urgency == "Immediate", f"Urgency is {question.urgency}, not Immediate")
		_assert(question.status == "Open", f"The escalation is {question.status}")
	finally:
		_restore_mode(ACTIVITY_2, original)
		_reset_competency()


def _raise_as(learner, text, **kwargs):
	from sparsh_los import escalation

	original_user = frappe.session.user
	try:
		frappe.set_user(learner)
		return escalation.raise_question(text, **kwargs)
	finally:
		frappe.set_user(original_user)


def check_urgency_default_and_freeze():
	from sparsh_los import escalation

	_make_learner(TEST_LEARNER)
	_delete_all("Sparsh Escalation Question", {"learner": TEST_LEARNER})
	frappe.db.commit()

	routine = _raise_as(TEST_LEARNER, "zzv a routine question", activity=ACTIVITY_1)
	_assert(
		frappe.db.get_value("Sparsh Escalation Question", routine, "urgency") == "Routine",
		"A question raised without urgency is not Routine",
	)
	urgent = _raise_as(TEST_LEARNER, "zzv an urgent question", activity=ACTIVITY_1, urgency="Immediate")
	_assert(
		frappe.db.get_value("Sparsh Escalation Question", urgent, "urgency") == "Immediate",
		"An explicit urgency was not stored",
	)
	_raises(
		lambda: _raise_as(TEST_LEARNER, "zzv a junk question", activity=ACTIVITY_1, urgency="Whenever"),
		"An unrecognised urgency was accepted",
		expect="not a recognised urgency",
	)

	escalation.answer(urgent, "Refer to the clinician.", "Private answer")
	frappe.db.commit()

	def change_urgency():
		doc = frappe.get_doc("Sparsh Escalation Question", urgent)
		doc.urgency = "Routine"
		doc.save(ignore_permissions=True)

	_refused(change_urgency, "Urgency was changed on an answered question", expect="already been answered")
	frappe.db.commit()


def check_open_queue_surfaces_urgency():
	from sparsh_los import escalation

	_make_learner(TEST_LEARNER)
	_delete_all("Sparsh Escalation Question", {"learner": TEST_LEARNER})
	frappe.db.commit()

	first = _raise_as(TEST_LEARNER, "zzv first, routine", activity=ACTIVITY_1)
	second = _raise_as(TEST_LEARNER, "zzv second, immediate", activity=ACTIVITY_1, urgency="Immediate")
	third = _raise_as(TEST_LEARNER, "zzv third, predates the column", activity=ACTIVITY_1)
	frappe.db.set_value("Sparsh Escalation Question", third, "urgency", None)
	frappe.db.commit()
	_assert(
		frappe.db.get_value("Sparsh Escalation Question", third, "urgency") is None,
		"The fixture for a pre-column row did not end up NULL",
	)

	queue = escalation.open_queue()
	by_name = {row["name"]: row for row in queue}
	for name in (first, second, third):
		_assert(name in by_name, f"{name} is missing from the open queue")
		_assert("urgency" in by_name[name], "Queue rows carry no urgency")
	_assert(by_name[first]["urgency"] == "Routine", f"first reads {by_name[first]['urgency']}")
	_assert(by_name[second]["urgency"] == "Immediate", f"second reads {by_name[second]['urgency']}")
	_assert(
		by_name[third]["urgency"] == "Routine",
		f"A NULL urgency reads as {by_name[third]['urgency']!r}, not Routine",
	)
	order = [row["name"] for row in queue]
	_assert(
		order.index(first) < order.index(second) < order.index(third),
		"The queue is no longer ordered by creation",
	)
	frappe.db.commit()


def check_escalation_event_logs_urgency():
	from sparsh_los import escalation, events

	_make_learner(TEST_LEARNER)
	_delete_all("Sparsh Escalation Question", {"learner": TEST_LEARNER})
	_delete_all("Sparsh Event", {"learner": TEST_LEARNER})
	frappe.db.commit()

	question = _raise_as(TEST_LEARNER, "zzv logged urgency", activity=ACTIVITY_1, urgency="Immediate")
	frappe.db.commit()

	def details():
		return frappe.get_all(
			"Sparsh Event",
			filters={"event_type": events.ESCALATION_OPENED, "reference_name": question},
			pluck="detail",
			order_by="creation asc",
		)

	logged = details()
	_assert(len(logged) == 1, f"{len(logged)} escalation_opened events for one question")
	_assert(
		"urgency=Immediate" in logged[0] and "reason=Unknown" in logged[0],
		f"The event detail does not carry urgency and reason: {logged[0]!r}",
	)

	# The Select refuses a junk value at insert, so the log's own guard is reached by
	# emitting for an in-memory document carrying one -- the way a widened field would.
	doc = frappe.get_doc("Sparsh Escalation Question", question)
	doc.urgency = "Whenever"
	doc.escalation_reason = "Something else"
	escalation._emit_opened(doc)
	frappe.db.commit()
	logged = details()
	_assert(len(logged) == 2, "The second emit was not recorded")
	_assert(
		logged[1] == "reason=other urgency=other",
		f"An unrecognised urgency was not logged as other: {logged[1]!r}",
	)
	_assert("Whenever" not in logged[1], "Caller text reached the event log")


# ---------------------------------------------------- phase 1: pathways, page
def check_pathway_status_is_respected():
	"""Only an Active pathway hands out work."""
	from sparsh_los import orchestrator

	_reset_competency()
	_delete_all("Sparsh Pathway", {"name": PATHWAY})
	frappe.db.commit()

	pathway = frappe.new_doc("Sparsh Pathway")
	pathway.pathway_id = PATHWAY
	pathway.title = "Verification pathway"
	pathway.status = "Draft"
	pathway.append(
		"steps", {"step_order": 1, "activity": ACTIVITY_1, "competency": COMPETENCY, "is_mandatory": 1}
	)
	pathway.insert(ignore_permissions=True)
	frappe.db.commit()

	draft = orchestrator.next_in_pathway(PATHWAY, LEARNER)
	_assert(draft.get("activity") is None, f"A Draft pathway handed out {draft.get('activity')}")
	_assert("draft" in (draft.get("message") or "").lower(), f"The refusal does not say why: {draft}")

	pathway.status = "Active"
	pathway.save(ignore_permissions=True)
	frappe.db.commit()
	active = orchestrator.next_in_pathway(PATHWAY, LEARNER)
	_assert(
		active.get("activity") == ACTIVITY_1,
		f"An Active pathway offered {active.get('activity')}, not its first step",
	)

	pathway.status = "Retired"
	pathway.save(ignore_permissions=True)
	frappe.db.commit()
	retired = orchestrator.next_in_pathway(PATHWAY, LEARNER)
	_assert(retired.get("activity") is None, f"A Retired pathway handed out {retired.get('activity')}")
	_assert("retired" in (retired.get("message") or "").lower(), f"The refusal does not say why: {retired}")
	frappe.db.commit()


def check_pilot_pathway_is_seeded_draft():
	"""The pilot pathway is the nine cases in order, mandatory, and Draft until a person activates it."""
	from sparsh_los import seed

	pre_existing = frappe.db.exists("Sparsh Pathway", seed.PILOT_PATHWAY)
	try:
		first = seed.load_pilot_pathway()
		if not pre_existing:
			_assert(first["created"] is True and first["steps"] == 9, f"First load reported {first}")

		doc = frappe.get_doc("Sparsh Pathway", seed.PILOT_PATHWAY)
		_assert(doc.status == "Draft", f"The pilot pathway was seeded {doc.status}, not Draft")
		steps = sorted(doc.steps, key=lambda s: s.step_order)
		activities = [frappe.db.get_value("Sparsh Activity", s.activity, "activity_id") for s in steps]
		_assert(
			activities == [f"SC-0{i}" for i in range(1, 10)],
			f"The pilot steps are {activities}",
		)
		_assert([s.step_order for s in steps] == list(range(1, 10)), "step_order is not 1..9")
		_assert(all(s.is_mandatory for s in steps), "A pilot step is optional")
		_assert(
			all(s.competency == frappe.db.get_value("Sparsh Activity", s.activity, "competency") for s in steps),
			"A step's competency disagrees with its activity's",
		)

		second = seed.load_pilot_pathway()
		_assert(second["created"] is False, f"A second load created again: {second}")
		_assert(
			frappe.db.count("Sparsh Pathway Step", {"parent": seed.PILOT_PATHWAY}) == 9,
			"A second load changed the step count",
		)
		_assert(
			frappe.db.get_value("Sparsh Pathway", seed.PILOT_PATHWAY, "status") == "Draft",
			"A second load changed the status",
		)
	finally:
		if not pre_existing:
			_delete_all("Sparsh Pathway", {"name": seed.PILOT_PATHWAY})
			frappe.db.commit()


def check_practice_page_prefers_the_assigned_pathway():
	"""An Active cohort's Active pathway decides the page; a learner in no cohort still gets work."""
	from sparsh_los.www import practice

	_make_learner(TEST_LEARNER)
	_make_learner(OTHER_LEARNER)
	_reset_competency()
	_clear_cohorts()
	_delete_all("Sparsh Pathway", {"name": PATHWAY})
	if not frappe.db.exists("Sparsh Activity", COHORT_ACTIVITY):
		doc = frappe.new_doc("Sparsh Activity")
		doc.activity_id = COHORT_ACTIVITY
		doc.title = "Cohort pathway step"
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
		{"step_order": 1, "activity": COHORT_ACTIVITY, "competency": COMPETENCY_2, "is_mandatory": 1},
	)
	pathway.insert(ignore_permissions=True)
	_new_cohort(COHORT_A, [TEST_LEARNER], status="Active", pathway=PATHWAY)
	frappe.db.commit()

	def render(user):
		context = frappe._dict()
		original_user = frappe.session.user
		try:
			frappe.set_user(user)
			practice.get_context(context)
		finally:
			frappe.set_user(original_user)
		return context

	try:
		assigned = render(TEST_LEARNER)
		_assert(assigned.pathway == PATHWAY, f"The page did not follow the cohort's pathway: {assigned.pathway!r}")
		_assert(
			assigned.next_up and assigned.next_up.get("activity") == COHORT_ACTIVITY,
			f"The cohort member was offered {assigned.next_up and assigned.next_up.get('activity')}, "
			f"not the pathway's step {COHORT_ACTIVITY}",
		)
		_assert(assigned.next_up.get("instruction"), "The pathway step carries no instruction")

		fallback = render(OTHER_LEARNER)
		_assert(fallback.pathway is None, f"A learner in no cohort was put on {fallback.pathway}")
		_assert(
			fallback.next_up and fallback.next_up.get("activity"),
			"A learner in no cohort was offered nothing to do",
		)
		_assert(
			fallback.next_up.get("activity") != COHORT_ACTIVITY,
			"The fallback offered the pathway step, so the two routes are indistinguishable here",
		)
	finally:
		_clear_cohorts()
		_delete_all("Sparsh Pathway", {"name": PATHWAY})
		frappe.db.commit()


def check_awaiting_a_person_excludes_reflections():
	"""The summary's queue figure and the queue itself agree once a reflection exists."""
	from sparsh_los import dashboard, review

	_make_learner(TEST_LEARNER)
	_reset_competency()
	# Two activities, not one re-authored mid-check: an activity carrying attempts that
	# are still waiting can no longer be turned into a Reflection at all, because doing
	# so used to remove that waiting work from the queue and the count together with
	# nobody seeing it. The control therefore lives on its own activity.
	original_two = _set_mode(ACTIVITY_2, "Reflection")
	original_one = _set_mode(ACTIVITY_1, "Human review")
	try:
		before = dashboard.programme_summary()["attempts_awaiting_a_person"]

		reflection = _submit(ACTIVITY_2, "zzv a reflection", as_user=TEST_LEARNER)
		control = _submit(ACTIVITY_1, "zzv work for a person", as_user=TEST_LEARNER)
		frappe.db.commit()

		waiting = {w["name"] for w in review.pending(limit=review.MAX_QUEUE)}
		_assert(len(waiting) < review.MAX_QUEUE, "The queue is at its cap, so its length cannot be compared")
		_assert(reflection["attempt"] not in waiting, "The reflection is queued")
		_assert(control["attempt"] in waiting, "The control attempt is not queued, so the fixture proves nothing")

		after = dashboard.programme_summary()["attempts_awaiting_a_person"]
		_assert(
			after == len(waiting),
			f"The summary says {after} attempts await a person; the queue holds {len(waiting)}",
		)
		# One reflection and one real attempt were added; only the real one may count.
		_assert(
			after == before + 1,
			f"Adding one reflection and one reviewable attempt moved the summary {before} -> {after}",
		)
	finally:
		_restore_mode(ACTIVITY_2, original_two)
		_restore_mode(ACTIVITY_1, original_one)


def check_reflection_does_not_swallow_waiting_work():
	"""An activity with attempts waiting cannot be re-authored into a Reflection.

	Both the queue and the summary exclude reflections by the activity's *current*
	mode, which is right for a new activity and wrong for one that already carries
	unreviewed attempts: flipping the mode made every one of them leave the queue and
	the count in the same instant, with no record that a learner was waiting on a
	verdict.
	"""
	_make_learner(TEST_LEARNER)
	_reset_competency()
	original = _set_mode(ACTIVITY_2, "Human review")
	try:
		result = _submit(ACTIVITY_2, "zzv an answer somebody must judge", as_user=TEST_LEARNER)
		frappe.db.commit()

		# Not `_raises`: it brackets the call in a savepoint, and `_set_mode` commits on
		# success. With the guard removed the commit destroys the savepoint and the
		# rollback then fails with "SAVEPOINT does not exist" -- the check still goes
		# red, but for a reason that says nothing about the guard. Refusal is asserted
		# directly, and the stored mode is read back so a silent success is caught too.
		try:
			_set_mode(ACTIVITY_2, "Reflection")
		except frappe.ValidationError as exc:
			_assert(
				"waiting for a reviewer" in str(exc).lower(),
				f"Refused, but for another reason: {exc}",
			)
		else:
			raise AssertionError(
				"An activity carrying waiting attempts was re-authored into a Reflection"
			)

		_assert(
			frappe.db.get_value("Sparsh Activity", ACTIVITY_2, "evaluation_mode") == "Human review",
			"The refused re-authoring was stored anyway",
		)

		from sparsh_los import review

		waiting = {w["name"] for w in review.pending(limit=review.MAX_QUEUE)}
		_assert(
			result["attempt"] in waiting,
			"The refused re-authoring still removed the attempt from the queue",
		)

		# Once the queue is clear the change is allowed: the guard protects waiting
		# work, it does not freeze the mode for ever.
		evidence = _new_evidence(ACTIVITY_2, "Pass")
		frappe.db.set_value("Sparsh Evidence", evidence.name, "attempt", result["attempt"])
		frappe.db.commit()
		_set_mode(ACTIVITY_2, "Reflection")
		_assert(
			frappe.db.get_value("Sparsh Activity", ACTIVITY_2, "evaluation_mode") == "Reflection",
			"The mode change was still refused after the queue was cleared",
		)
	finally:
		_restore_mode(ACTIVITY_2, original)


# ------------------------------------------------------------ phase 1: source
def check_translations_are_imported():
	"""Every bare `_(...)` call sits in a module that imports `_` from frappe.

	`ast.parse` accepts a module that calls a name it never binds; the NameError
	arrives only when the guarded line runs, and a refusal becomes a 500. Walked as an
	AST so a `_` inside a string or comment does not count and `frappe._(...)` is
	recognised as the attribute call it is.
	"""
	import ast
	import pathlib

	root = pathlib.Path(frappe.get_app_path("sparsh_los"))
	offenders = []
	scanned = 0
	bare_callers = 0
	for path in root.rglob("*.py"):
		# `._name.py` is a macOS AppleDouble sidecar the install tar carries into the
		# container; it is metadata, not source, and not even UTF-8.
		if "__pycache__" in path.parts or path.name.startswith("._"):
			continue
		scanned += 1
		tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))

		binds_underscore = False
		for node in ast.walk(tree):
			if isinstance(node, ast.ImportFrom) and any(a.asname == "_" or (a.name == "_" and not a.asname) for a in node.names):
				binds_underscore = True
			elif isinstance(node, ast.Import) and any(a.asname == "_" for a in node.names):
				binds_underscore = True
			elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == "_":
				binds_underscore = True
			elif isinstance(node, ast.Assign) and any(
				isinstance(t, ast.Name) and t.id == "_" for t in node.targets
			):
				binds_underscore = True

		bare_calls = [
			node.lineno
			for node in ast.walk(tree)
			if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "_"
		]
		if bare_calls:
			bare_callers += 1
		if bare_calls and not binds_underscore:
			offenders.append(f"{path.relative_to(root)}:{bare_calls[0]}")

	_assert(scanned >= 40, f"The translation scan only saw {scanned} files; it is not scanning")
	_assert(bare_callers >= 10, f"Only {bare_callers} modules call _(...); the scan is not finding calls")
	_assert(
		not offenders,
		"Bare _(...) in a module that never imports _ from frappe -- a NameError the moment "
		f"the guard fires: {', '.join(offenders)}",
	)


# ------------------------------------------------------------- phase 2: analytics
INACTIVE_LEARNER = "zzv-inactive@example.invalid"
REFLECTION_ACTIVITY = PREFIX + "ACT-REFL"
# Three criteria whose wording is distinctive enough that a summary carrying any of it
# would be caught: the learner can read their own Evidence, so the text must not reach it.
RUBRIC_CRITERIA = (
	"Names the concern in plain words\n"
	"Asks what the caregiver already does\n"
	"Agrees one next step together"
)


def _summary():
	from sparsh_los import dashboard

	return dashboard.programme_summary()


def _author(activity_name, **fields):
	"""Write authored fields on an activity. Returns the originals, for `_author(name, **original)`."""
	activity = frappe.get_doc("Sparsh Activity", activity_name)
	original = {field: activity.get(field) for field in fields}
	for field, value in fields.items():
		activity.set(field, value)
	activity.save(ignore_permissions=True)
	frappe.db.commit()
	return original


def _ensure_activity(name, competency=COMPETENCY, **fields):
	if frappe.db.exists("Sparsh Activity", name):
		return frappe.get_doc("Sparsh Activity", name)
	activity = frappe.new_doc("Sparsh Activity")
	activity.activity_id = name
	activity.title = "Verification Activity"
	activity.competency = competency
	activity.activity_type = "Knowledge check"
	activity.instruction = "Verification instruction."
	activity.version = 1
	activity.hints = "First hint.\nSecond hint."
	for field, value in fields.items():
		activity.set(field, value)
	activity.insert(ignore_permissions=True)
	frappe.db.commit()
	return activity


def _reconciles(summary, label):
	inactivity = summary["inactivity"]
	_assert(
		inactivity["inactive"] + summary["active_in_period"] == summary["enrolled"],
		f"{label}: inactive {inactivity['inactive']} + active {summary['active_in_period']} "
		f"!= enrolled {summary['enrolled']}",
	)


def _fresh_inactive_learner():
	"""No account, no attempt, no event: the one learner whose last activity is genuinely unknown."""
	_delete_all("Sparsh Attempt", {"learner": INACTIVE_LEARNER})
	_delete_all("Sparsh Event", {"learner": INACTIVE_LEARNER})
	if frappe.db.exists("User", INACTIVE_LEARNER):
		frappe.delete_doc("User", INACTIVE_LEARNER, force=True, ignore_permissions=True)
	frappe.db.commit()


def _inactivity_row(inactivity, learner):
	rows = [r for r in inactivity["learners"] if r["learner"] == learner]
	_assert(len(rows) == 1, f"{learner} appears {len(rows)} time(s) in the inactive list")
	return rows[0]


def check_inactive_reconciles():
	"""inactive + active_in_period == enrolled exactly, and inactive is counted from Attempts.

	A learner who opened a session and answered nothing has been seen, not active: the
	Event moves their last-activity date and nothing else. Counting inactivity from
	Events would break the identity the moment somebody logged in without working.
	"""
	from sparsh_los import events

	_fresh_inactive_learner()
	# Any mode records an attempt; Human review is the one no other check's leftovers
	# can turn into a refusal (a Numeric key would refuse the text below outright).
	original = _set_mode(ACTIVITY_1, "Human review")
	try:
		base = _summary()
		_assert(
			base["inactivity"]["inactive"] is not None,
			"No account holds the learner role, so inactivity is undefined and this check cannot run",
		)
		_reconciles(base, "baseline")

		_make_learner(INACTIVE_LEARNER)
		frappe.db.commit()
		quiet = _summary()
		_assert(quiet["enrolled"] - base["enrolled"] == 1, "The fixture account did not enrol")
		_assert(
			quiet["inactivity"]["inactive"] - base["inactivity"]["inactive"] == 1,
			f"A learner with no attempt did not move inactive: "
			f"{base['inactivity']['inactive']} -> {quiet['inactivity']['inactive']}",
		)
		row = _inactivity_row(quiet["inactivity"], INACTIVE_LEARNER)
		_assert(
			row["last_activity"] is None and row["source"] is None,
			f"A learner never seen is reported with a last activity: {row}",
		)
		_reconciles(quiet, "after enrolment")

		events.emit(events.SESSION_STARTED, learner=INACTIVE_LEARNER)
		frappe.db.commit()
		seen = _summary()
		_assert(
			seen["inactivity"]["inactive"] - base["inactivity"]["inactive"] == 1,
			f"A session event with no attempt changed the inactive count: "
			f"{quiet['inactivity']['inactive']} -> {seen['inactivity']['inactive']}",
		)
		_assert(
			seen["active_in_period"] == base["active_in_period"],
			"A session event with no attempt counted as active",
		)
		_reconciles(seen, "after a session event")

		_submit(ACTIVITY_1, "zzv one attempt ends inactivity", as_user=INACTIVE_LEARNER)
		frappe.db.commit()
		back = _summary()
		_assert(
			back["inactivity"]["inactive"] == base["inactivity"]["inactive"],
			f"One attempt did not return inactive to its baseline: "
			f"{base['inactivity']['inactive']} -> {back['inactivity']['inactive']}",
		)
		_assert(
			back["active_in_period"] - base["active_in_period"] == 1,
			f"One attempt did not move active_in_period: {base['active_in_period']} -> {back['active_in_period']}",
		)
		_assert(
			not [r for r in back["inactivity"]["learners"] if r["learner"] == INACTIVE_LEARNER],
			"A learner who has just attempted something is still listed as inactive",
		)
		_reconciles(back, "after an attempt")
		_assert(
			back["inactivity"]["proportion"]
			== round(back["inactivity"]["inactive"] / back["enrolled"], 4),
			f"The inactive proportion is not inactive/enrolled: {back['inactivity']}",
		)
	finally:
		_restore_mode(ACTIVITY_1, original)
		_delete_all("Sparsh Attempt", {"learner": INACTIVE_LEARNER})
		frappe.db.commit()


def check_last_activity_date_from_event():
	"""An inactive learner's last-seen date takes the Event when it is all there is."""
	from sparsh_los import events

	_fresh_inactive_learner()
	_make_learner(INACTIVE_LEARNER)
	frappe.db.commit()
	base = _summary()["inactivity"]
	_assert(
		_inactivity_row(base, INACTIVE_LEARNER)["source"] is None,
		"The fixture learner already has a last activity, so the event cannot be shown to supply it",
	)

	events.emit(events.SESSION_STARTED, learner=INACTIVE_LEARNER)
	frappe.db.commit()
	after = _summary()["inactivity"]
	row = _inactivity_row(after, INACTIVE_LEARNER)
	_assert(
		row["source"] == "event" and row["last_activity"] is not None,
		f"A session event did not supply the last-activity date: {row}",
	)
	_assert(
		frappe.utils.getdate(row["last_activity"]) == frappe.utils.getdate(),
		f"The last-activity date is not the event's date: {row}",
	)
	_assert(
		after["inactive_with_event_in_period"] - base["inactive_with_event_in_period"] == 1,
		f"Seen-but-idle did not move: {base['inactive_with_event_in_period']} -> "
		f"{after['inactive_with_event_in_period']}",
	)
	_assert(after["inactive"] == base["inactive"], "An event changed the inactive count")


def check_refresher_repeated():
	"""A second refresher on the same learner and competency is one repeat, not two."""
	from sparsh_los import refresher

	_reset_competency()
	try:
		base = _summary()["refreshers"]
		first = refresher.assign(LEARNER, COMPETENCY, refresher.TIME_ELAPSED, "zzv first refresher")
		second = refresher.assign(LEARNER, COMPETENCY, refresher.RULE_CHANGED, "zzv second refresher")
		frappe.db.commit()
		_assert(first and second, f"The fixture did not produce two assignments: {first}, {second}")

		after = _summary()["refreshers"]
		_assert(
			after["issued"] - base["issued"] == 2,
			f"Two assignments moved issued {base['issued']} -> {after['issued']}",
		)
		_assert(
			after["repeated"] - base["repeated"] == 1,
			f"A second refresher on one pair moved repeated {base['repeated']} -> {after['repeated']}, expected +1",
		)
		_assert(
			after["open_now"] - base["open_now"] == 2,
			f"Two open assignments moved open_now {base['open_now']} -> {after['open_now']}",
		)
		_assert(after["completed"] == base["completed"], "Issuing a refresher counted as completing one")
	finally:
		_reset_competency()


def check_refresher_overdue_is_undefined():
	"""There is no due date, so overdue is None with a reason -- never a zero that reads as 'none overdue'."""
	report = _summary()["refreshers"]
	_assert("overdue" in report, "The refresher block does not report overdue at all")
	_assert(report["overdue"] is None, f"Overdue was counted as {report['overdue']!r} with no due date to count from")
	reason = report.get("overdue_undefined_because")
	_assert(
		isinstance(reason, str) and reason.strip(),
		f"Overdue is undefined but no reason is given: {reason!r}",
	)
	_assert(
		not frappe.get_meta("Sparsh Refresher Assignment").has_field("due_on")
		and not frappe.get_meta("Sparsh Refresher Assignment").has_field("due_date"),
		"A due-date field now exists on Refresher Assignment; overdue can be counted and this check is stale",
	)


def check_escalation_turnaround_median():
	"""Median hours from raised to answered, over the questions answered in the period."""
	from sparsh_los import escalation

	_make_learner(TEST_LEARNER)
	# Every question on this bench is the harness's own, and the median is a whole-table
	# figure, so the table is emptied rather than the fixture asserted as a delta.
	_delete_all("Sparsh Escalation Question", {"learner": ("like", PREFIX.lower() + "%")})
	frappe.db.commit()

	base = _summary()["escalation_turnaround"]
	_assert(
		base["answered_in_period"] == 0,
		f"{base['answered_in_period']} answered question(s) not raised by this harness are inside "
		"the window; the median cannot be isolated on this site",
	)
	_assert(
		base["median_hours"] is None and base["median_undefined_because"],
		f"An empty period reports a median: {base}",
	)

	question = _raise_as(TEST_LEARNER, "zzv how long does an answer take")
	two_hours_ago = frappe.utils.add_to_date(frappe.utils.now_datetime(), hours=-2)
	frappe.db.set_value("Sparsh Escalation Question", question, "raised_at", two_hours_ago)
	frappe.db.commit()

	waiting = _summary()["escalation_turnaround"]
	_assert(
		waiting["still_open_raised_in_period"] == 1 and waiting["answered_in_period"] == 0,
		f"An open question raised in the period is misreported: {waiting}",
	)

	escalation.answer(question, "zzv answered two hours later", "Private answer")
	frappe.db.commit()
	turnaround = _summary()["escalation_turnaround"]
	_assert(turnaround["answered_in_period"] == 1, f"The answered question was not counted: {turnaround}")
	_assert(turnaround["answered_without_raised_at"] == 0, f"A timed question read as untimed: {turnaround}")
	_assert(
		turnaround["median_hours"] is not None and 1.9 <= turnaround["median_hours"] <= 2.1,
		f"A question answered two hours after it was raised has median_hours {turnaround['median_hours']}",
	)
	_assert(turnaround["answered_before_raised"] == 0, f"The clocks were read backwards: {turnaround}")
	_assert(turnaround["median_undefined_because"] is None, "A defined median still carries an undefined reason")
	_assert(turnaround["still_open_raised_in_period"] == 0, "An answered question is still counted open")


def _state_change_bucket(report):
	return report["by_competency"].get(
		COMPETENCY, {"changes": 0, "improved": 0, "regressed": 0, "lateral": 0, "transitions": {}}
	)


def check_state_change_parsed():
	"""Transitions are parsed from the event log and ranked: Practising -> Demonstrated is an improvement."""
	_reset_competency()
	base = _summary()["competency_state_changes"]
	b0 = _state_change_bucket(base)

	# An assisted pass first, so the second transition starts from Practising rather
	# than from nothing: ranking Demonstrated level with Practising would read it as
	# lateral, and only a transition out of Practising can show that.
	_new_evidence(None, "Pass", assistance_level=1)
	_new_evidence(ACTIVITY_1, "Pass")
	frappe.db.commit()
	_assert(_state() == DEMONSTRATED, f"The fixture did not reach Demonstrated: {_state()}")

	after = _summary()["competency_state_changes"]
	b1 = _state_change_bucket(after)
	for key in ("none -> Practising", "Practising -> Demonstrated"):
		_assert(
			b1["transitions"].get(key, 0) - b0["transitions"].get(key, 0) == 1,
			f"The transition {key!r} was not parsed once: {b0['transitions']} -> {b1['transitions']}",
		)
	_assert(b1["changes"] - b0["changes"] == 2, f"Two transitions moved changes {b0['changes']} -> {b1['changes']}")
	_assert(
		b1["improved"] - b0["improved"] == 2,
		f"Two upward transitions moved improved {b0['improved']} -> {b1['improved']}",
	)
	_assert(b1["regressed"] == b0["regressed"], "An upward transition was counted as a regression")
	_assert(b1["lateral"] == b0["lateral"], "An upward transition was counted as lateral")
	_assert(after["unparsed"] == base["unparsed"], f"A well-formed detail was reported unparsed: {after['unparsed']}")
	_assert(after["events"] - base["events"] == 2, f"Two events moved the count {base['events']} -> {after['events']}")


def _attempt_bucket(report):
	return report["by_competency"].get(
		COMPETENCY,
		{"attempts": 0, "retries": 0, "assisted": 0, "critical_errors": 0, "hint_levels": {}, "outcomes": {}},
	)


def _attempts_sum_to_total(report, label):
	total = sum(b["attempts"] for b in report["by_competency"].values()) + report["unattributed"]
	_assert(
		total == report["attempts"],
		f"{label}: per-competency attempts {total - report['unattributed']} + unattributed "
		f"{report['unattributed']} != attempts {report['attempts']}",
	)


def check_attempts_by_competency_sum():
	"""Per-competency attempts, hint levels and the unattributed remainder sum to the total."""
	_reset_competency()
	original = _author(ACTIVITY_1, evaluation_mode="Deterministic", expected_response="level two")
	orphan = PREFIX + "GONE"
	try:
		base = _summary()["attempts_by_competency"]
		_attempts_sum_to_total(base, "baseline")
		b0 = _attempt_bucket(base)

		wrong = _submit(ACTIVITY_1, "level four")
		_assert(wrong["outcome"] == "Fail" and wrong["hint_level"] == 1, f"The first wrong answer did not earn hint 1: {wrong}")
		assisted = _submit(ACTIVITY_1, "level five")
		frappe.db.commit()
		_assert(
			frappe.db.get_value("Sparsh Attempt", assisted["attempt"], "hint_level_used") == 1,
			"The second attempt did not record the hint it followed",
		)

		after = _summary()["attempts_by_competency"]
		b1 = _attempt_bucket(after)
		_assert(b1["attempts"] - b0["attempts"] == 2, f"Two attempts moved the competency {b0['attempts']} -> {b1['attempts']}")
		_assert(after["attempts"] - base["attempts"] == 2, f"Two attempts moved the total {base['attempts']} -> {after['attempts']}")
		for level in (0, 1):
			_assert(
				b1["hint_levels"].get(level, 0) - b0["hint_levels"].get(level, 0) == 1,
				f"Hint level {level} bucket did not move by one: {b0['hint_levels']} -> {b1['hint_levels']}",
			)
		_assert(b1["assisted"] - b0["assisted"] == 1, f"One assisted attempt moved assisted {b0['assisted']} -> {b1['assisted']}")
		_assert(b1["retries"] - b0["retries"] == 1, f"One retry moved retries {b0['retries']} -> {b1['retries']}")
		_assert(
			b1["outcomes"].get("Fail", 0) - b0["outcomes"].get("Fail", 0) == 2,
			f"Two failures moved the Fail outcome {b0['outcomes']} -> {b1['outcomes']}",
		)
		_assert(b1["critical_errors"] == b0["critical_errors"], "A plain wrong answer counted as a critical error")
		_assert(after["unattributed"] == base["unattributed"], "An attempt on a live activity was unattributed")
		_attempts_sum_to_total(after, "after two attempts")

		# An attempt whose activity has gone. Written the way such a row arrives -- the
		# activity deleted underneath it -- rather than through a controller that
		# would refuse it.
		frappe.db.sql(
			"update `tabSparsh Attempt` set activity = %s where name = %s", (orphan, assisted["attempt"])
		)
		frappe.db.commit()
		orphaned = _summary()["attempts_by_competency"]
		_assert(
			orphaned["unattributed"] - after["unattributed"] == 1,
			f"An attempt on a missing activity was not counted unattributed: "
			f"{after['unattributed']} -> {orphaned['unattributed']}",
		)
		_assert(
			_attempt_bucket(orphaned)["attempts"] - b0["attempts"] == 1,
			"The orphaned attempt is still credited to its old competency",
		)
		_assert(orphaned["attempts"] == after["attempts"], "Orphaning an attempt changed the total")
		_attempts_sum_to_total(orphaned, "with an orphaned attempt")
	finally:
		_author(ACTIVITY_1, **original)
		_delete_all("Sparsh Attempt", {"activity": orphan})
		_reset_competency()


def _pathway_fixture(members):
	"""An Active cohort of `members` on an Active two-step pathway: ACTIVITY_1, then ACTIVITY_2."""
	_clear_cohorts()
	_delete_all("Sparsh Pathway", {"name": PATHWAY})
	frappe.db.commit()
	pathway = frappe.new_doc("Sparsh Pathway")
	pathway.pathway_id = PATHWAY
	pathway.title = "Verification pathway"
	pathway.status = "Active"
	for order, activity in enumerate((ACTIVITY_1, ACTIVITY_2), start=1):
		pathway.append(
			"steps", {"step_order": order, "activity": activity, "competency": COMPETENCY, "is_mandatory": 1}
		)
	pathway.insert(ignore_permissions=True)
	_new_cohort(COHORT_A, members, status="Active", pathway=PATHWAY)
	frappe.db.commit()


def _clear_pathway_fixture():
	_clear_cohorts()
	_delete_all("Sparsh Pathway", {"name": PATHWAY})
	frappe.db.commit()


def _fixture_pathway_bucket(report):
	_assert(PATHWAY in report["by_pathway"], f"The fixture pathway is not reported: {sorted(report['by_pathway'])}")
	return report["by_pathway"][PATHWAY]


def check_pathway_completion():
	"""Steps are assigned through Active cohorts and completed only by an unaided pass."""
	_make_learner(TEST_LEARNER)
	_reset_competency()
	base = _summary()["pathway_completion"]
	_pathway_fixture([TEST_LEARNER])
	try:
		nothing = _summary()["pathway_completion"]
		_assert(
			_fixture_pathway_bucket(nothing) == {"learners": 1, "steps_assigned": 2, "steps_completed": 0},
			f"A fresh member of a two-step pathway is misreported: {_fixture_pathway_bucket(nothing)}",
		)
		_assert(
			nothing["steps_assigned"] - base["steps_assigned"] == 2,
			f"Two assigned steps moved steps_assigned {base['steps_assigned']} -> {nothing['steps_assigned']}",
		)
		_assert(
			nothing["learners_assigned"] - base["learners_assigned"] == 1,
			f"One member moved learners_assigned {base['learners_assigned']} -> {nothing['learners_assigned']}",
		)
		_assert(nothing["active_cohorts"] - base["active_cohorts"] == 1, "The fixture cohort is not counted Active")

		_new_evidence(ACTIVITY_1, "Pass", learner=TEST_LEARNER)
		frappe.db.commit()
		one = _summary()["pathway_completion"]
		_assert(
			_fixture_pathway_bucket(one)["steps_completed"] == 1,
			f"An unaided pass on step 1 did not complete it: {_fixture_pathway_bucket(one)}",
		)
		_assert(one["steps_completed"] - base["steps_completed"] == 1, "The global completed figure did not move by one")
		expected = round((base["steps_completed"] + 1) / (base["steps_assigned"] + 2), 4)
		_assert(
			one["proportion"] == expected,
			f"proportion is {one['proportion']}, expected {expected} (0.5 on a bench with no other Active cohort)",
		)

		_new_evidence(ACTIVITY_2, "Pass", assistance_level=1, learner=TEST_LEARNER)
		frappe.db.commit()
		two = _summary()["pathway_completion"]
		_assert(
			_fixture_pathway_bucket(two)["steps_completed"] == 1,
			f"An assisted pass on step 2 completed it: {_fixture_pathway_bucket(two)}",
		)
		_assert(
			two["steps_passed_assisted_only"] - base["steps_passed_assisted_only"] == 1,
			f"The assisted pass was not reported separately: "
			f"{base['steps_passed_assisted_only']} -> {two['steps_passed_assisted_only']}",
		)
		_assert(
			two["learners_completed_all_steps"] == base["learners_completed_all_steps"],
			"A learner with an assisted step read as having completed the pathway",
		)

		_new_evidence(ACTIVITY_2, "Pass", learner=TEST_LEARNER)
		frappe.db.commit()
		done = _summary()["pathway_completion"]
		_assert(
			_fixture_pathway_bucket(done)["steps_completed"] == 2,
			f"An unaided pass on step 2 did not complete it: {_fixture_pathway_bucket(done)}",
		)
		_assert(
			done["learners_completed_all_steps"] - base["learners_completed_all_steps"] == 1,
			"Completing both steps did not count the learner as finished",
		)
		_assert(
			done["steps_passed_assisted_only"] == base["steps_passed_assisted_only"],
			"A step now passed unaided is still counted as assisted-only",
		)
	finally:
		_clear_pathway_fixture()
		_reset_competency()


def check_rejected_pass_does_not_complete_a_step():
	"""A pass a reviewer rejected stops completing the step it once completed."""
	_make_learner(TEST_LEARNER)
	_reset_competency()
	_pathway_fixture([TEST_LEARNER])
	try:
		evidence = _new_evidence(ACTIVITY_1, "Pass", learner=TEST_LEARNER)
		frappe.db.commit()
		before = _summary()["pathway_completion"]
		_assert(
			_fixture_pathway_bucket(before)["steps_completed"] == 1,
			"The positive control did not complete the step, so a rejection cannot be shown to undo it",
		)

		review = frappe.new_doc("Sparsh Human Review")
		review.evidence = evidence.name
		review.review_status = "Rejected"
		review.reviewer_comments = "zzv not a demonstration of the competency"
		review.insert(ignore_permissions=True)
		review.submit()
		frappe.db.commit()
		_assert(
			frappe.db.get_value("Sparsh Evidence", evidence.name, "human_review_status") == REJECTED,
			"The rejection did not reach the evidence, so the fixture proves nothing",
		)

		after = _summary()["pathway_completion"]
		_assert(
			_fixture_pathway_bucket(after)["steps_completed"] == 0,
			f"A rejected pass still completes the step: {_fixture_pathway_bucket(after)}",
		)
		_assert(
			after["steps_passed_assisted_only"] == before["steps_passed_assisted_only"],
			"A rejected pass crept into the assisted-only figure",
		)
	finally:
		_clear_pathway_fixture()
		_reset_competency()


def check_no_model_share():
	"""Two readings of the no-model-call share, and they are reported disagreeing when they do."""
	_make_learner(TEST_LEARNER)
	_reset_competency()
	# The share is a whole-window figure. Every attempt and ledger row on this bench is
	# the harness's own, so the window is emptied and the shape asserted exactly.
	_delete_all("Sparsh Attempt", {"learner": ("like", PREFIX.lower() + "%")})
	_delete_all("Sparsh Attempt", {"activity": ("like", PREFIX + "%")})
	_delete_all("Sparsh Model Interaction", {"provider": ("like", "zzv%")})
	frappe.db.commit()
	original_one = _set_mode(ACTIVITY_1, "Human review")
	original_two = _set_mode(ACTIVITY_2, "AI-assisted")
	try:
		base = _summary()["no_model_call_share"]
		# A whole-window figure asserted to an exact value needs the window to hold only
		# this harness's work. On a site carrying the demonstration cohort it does not,
		# and there is no way to scope the summary to one learner. Skipped rather than
		# failed: nothing is wrong with the engine, the check simply cannot be run here.
		if base["attempts"]:
			raise Inapplicable(
				f"{base['attempts']} attempt(s) in the window were not made by this "
				f"harness (the demonstration cohort, most likely), so an exact share "
				f"cannot be isolated"
			)
		if base["by_ledger"]["model_interactions_in_period"]:
			raise Inapplicable(
				f"{base['by_ledger']['model_interactions_in_period']} ledger row(s) in "
				f"the window were not written by this harness"
			)
		_assert(base["by_mode"]["share"] is None and base["share_undefined_because"], f"An empty window has a share: {base}")

		_submit(ACTIVITY_1, "zzv work for a person", as_user=TEST_LEARNER)
		frappe.db.commit()
		human = _summary()["no_model_call_share"]
		_assert(
			human["by_mode"] == {"numerator": 1, "denominator": 1, "share": 1.0},
			f"One Human-review attempt does not read as 1.0 by mode: {human['by_mode']}",
		)
		_assert(
			human["by_ledger"]["numerator"] == 1 and human["by_ledger"]["share"] == 1.0,
			f"One attempt and an empty ledger do not read as 1.0 by ledger: {human['by_ledger']}",
		)
		_assert(human["readings_agree"] is True and human["disagreement"] is None, f"Agreeing readings reported otherwise: {human}")
		_assert(human["attempts_on_unknown_activity"] == 0, "The attempt's activity was not found")

		ai = _submit(ACTIVITY_2, "zzv work an unbuilt model would judge", as_user=TEST_LEARNER)
		frappe.db.commit()
		_assert(ai["outcome"] == "Not Evaluated", f"The AI-assisted attempt was scored: {ai['outcome']}")
		mixed = _summary()["no_model_call_share"]
		_assert(
			mixed["by_mode"] == {"numerator": 1, "denominator": 2, "share": 0.5},
			f"An AI-assisted attempt did not lower the by-mode reading: {mixed['by_mode']}",
		)
		_assert(
			mixed["by_ledger"]["numerator"] == 2 and mixed["by_ledger"]["share"] == 1.0,
			f"With no model call recorded the ledger reading moved: {mixed['by_ledger']}",
		)
		_assert(mixed["readings_agree"] is False, "One AI-assisted attempt against an empty ledger read as agreement")
		_assert(
			isinstance(mixed["disagreement"], str) and "1 attempt" in mixed["disagreement"],
			f"The disagreement is not stated: {mixed['disagreement']!r}",
		)
	finally:
		_restore_mode(ACTIVITY_1, original_one)
		_restore_mode(ACTIVITY_2, original_two)
		_delete_all("Sparsh Attempt", {"learner": TEST_LEARNER})
		frappe.db.commit()


# ------------------------------------------------------ phase 3: numeric evaluator
def _attempt_count(learner, activity):
	return frappe.db.count("Sparsh Attempt", {"learner": learner, "activity": activity})


def check_numeric_scores_within_tolerance():
	"""|response - expected| <= tolerance, in decimal arithmetic, with the unit ignored."""
	_reset_competency()
	original_one = _author(ACTIVITY_1, evaluation_mode="Numeric validation", expected_value="72.5", tolerance=0.5)
	original_two = _author(ACTIVITY_2, evaluation_mode="Numeric validation", expected_value="2.5", tolerance=0.2)
	try:
		close = _submit(ACTIVITY_1, "72.9 kg")
		_assert(close["outcome"] == "Pass", f"72.9 against 72.5 +/- 0.5 was {close['outcome']}")
		_assert(close.get("evidence"), "A numeric pass produced no evidence")
		_assert(
			frappe.db.get_value("Sparsh Evidence", close["evidence"], "assistance_level") == 0,
			"An unaided numeric pass was recorded as assisted",
		)

		far = _submit(ACTIVITY_1, "73.2")
		_assert(far["outcome"] == "Fail", f"73.2 against 72.5 +/- 0.5 was {far['outcome']}")
		_assert(far["hint_level"] == 1 and far["hint"] == "First hint.", f"A numeric miss did not earn the first hint: {far}")
		_assert("evidence" not in far, "A numeric miss produced evidence")
		_assert(not far["critical_error"], "A numeric miss was flagged critical")

		# 2.7 - 2.5 in binary floating point is a shade over 0.2; the author meant 2.7 to pass.
		edge = _submit(ACTIVITY_2, "2.7")
		_assert(
			edge["outcome"] == "Pass",
			f"2.7 against 2.5 +/- 0.2 was {edge['outcome']}: the tolerance is being compared in binary floating point",
		)
		_assert(_state() == MASTERED, f"Two unaided numeric passes on distinct activities gave {_state()}")
	finally:
		_author(ACTIVITY_1, **original_one)
		_author(ACTIVITY_2, **original_two)
		_reset_competency()


def check_numeric_zero_is_an_answer():
	"""'0' is an answer and '' is the absence of one; the field type is what keeps them apart."""
	_reset_competency()
	original = _author(ACTIVITY_1, evaluation_mode="Numeric validation", expected_value="0", tolerance=0)
	try:
		_assert(
			frappe.get_meta("Sparsh Activity").get_field("expected_value").fieldtype == "Data",
			"expected_value is not a Data field; a numeric column stores blank as 0 and 'not agreed' becomes 'zero'",
		)
		zero = _submit(ACTIVITY_1, "0")
		_assert(zero["outcome"] == "Pass", f"'0' against an expected value of 0 was {zero['outcome']}")

		_author(ACTIVITY_1, expected_value="")
		stored = frappe.db.get_value("Sparsh Activity", ACTIVITY_1, "expected_value")
		_assert(stored in (None, ""), f"A blank expected value was stored as {stored!r}")
		blank = _submit(ACTIVITY_1, "0")
		_assert(
			blank["outcome"] == "Not Evaluated",
			f"With no expected value agreed, '0' was scored {blank['outcome']}",
		)
		_assert("evidence" not in blank, "An unscorable numeric activity produced evidence")
		_assert("reviewed by a person" in blank["message"].lower(), f"The learner was told something else: {blank['message']}")
	finally:
		_author(ACTIVITY_1, **original)
		_reset_competency()


def check_numeric_non_answer_is_refused_not_recorded():
	"""Text, or two numbers, is not an answer: refused before an Attempt exists, so the ladder does not move."""
	_reset_competency()
	original = _author(ACTIVITY_1, evaluation_mode="Numeric validation", expected_value="72.5", tolerance=0.5)
	try:
		before = _attempt_count(LEARNER, ACTIVITY_1)
		for response in ("abc", "120/80"):
			_raises(
				lambda: _submit(ACTIVITY_1, response),
				f"{response!r} was graded on an activity that asked for one number",
				expect="expects a single number",
			)
		_assert(
			_attempt_count(LEARNER, ACTIVITY_1) == before,
			f"A refused response was recorded as an attempt: {before} -> {_attempt_count(LEARNER, ACTIVITY_1)}",
		)

		wrong = _submit(ACTIVITY_1, "80")
		frappe.db.commit()
		_assert(wrong["outcome"] == "Fail", f"A wrong number was {wrong['outcome']}")
		_assert(
			frappe.db.get_value("Sparsh Attempt", wrong["attempt"], "hint_level_used") == 0,
			"The refused responses climbed the hint ladder for the answer that followed",
		)
		_assert(
			frappe.db.get_value("Sparsh Attempt", wrong["attempt"], "retry_index") == 0,
			"The refused responses counted as retries",
		)
	finally:
		_author(ACTIVITY_1, **original)
		_reset_competency()


def _link_rule(status):
	rule = _new_rule(1, status=status)
	competency = frappe.get_doc("Sparsh Competency", COMPETENCY)
	competency.set("linked_rules", [])
	competency.append("linked_rules", {"rule": rule.name})
	competency.save(ignore_permissions=True)
	frappe.db.commit()
	return rule


def check_numeric_obeys_the_rule_gate():
	"""Under a Draft rule the arithmetic does not run: nothing scores, and nothing is refused either."""
	_reset_competency()
	rule = _link_rule("Draft")
	original = _author(ACTIVITY_1, evaluation_mode="Numeric validation", expected_value="72.5", tolerance=0.5)
	try:
		gated = _submit(ACTIVITY_1, "72.5")
		_assert(gated["outcome"] == "Not Evaluated", f"A Draft rule let the arithmetic score: {gated['outcome']}")
		_assert("evidence" not in gated, "A Draft rule let a numeric pass become evidence")

		text = _submit(ACTIVITY_1, "abc")
		frappe.db.commit()
		_assert(
			text["outcome"] == "Not Evaluated"
			and frappe.db.get_value("Sparsh Attempt", text["attempt"], "outcome") == "Not Evaluated",
			f"Under a Draft rule a non-numeric response was not simply recorded for a person: {text}",
		)

		# Positive control: the same activity scores once the rule is Validated.
		frappe.db.set_value("Sparsh Source of Truth Rule", rule.name, "status", "Validated")
		frappe.db.commit()
		scored = _submit(ACTIVITY_1, "72.5")
		_assert(scored["outcome"] == "Pass", f"A Validated rule did not let the arithmetic score: {scored['outcome']}")
	finally:
		_author(ACTIVITY_1, **original)
		_reset_competency()


def check_numeric_critical_marker_still_bites():
	"""A declared critical error is judged before the arithmetic, number or no number."""
	_reset_competency()
	original = _author(
		ACTIVITY_1,
		evaluation_mode="Numeric validation",
		expected_value="72.5",
		tolerance=0.5,
		critical_markers="stop the medicine",
	)
	try:
		unsafe = _submit(ACTIVITY_1, "I would stop the medicine")
		frappe.db.commit()
		_assert(
			unsafe["outcome"] == "Fail" and unsafe["critical_error"] == 1,
			f"A critical marker in a non-numeric response was not a critical Fail: {unsafe}",
		)
		_assert(unsafe.get("escalation"), "A critical numeric response reached no person")
		_assert(
			frappe.db.get_value("Sparsh Evidence", unsafe["evidence"], "critical_error") == 1,
			"The critical error was not written to evidence",
		)
		_assert(
			frappe.db.get_value("Sparsh Escalation Question", unsafe["escalation"], "escalation_reason")
			== "Safety critical",
			"The escalation does not say it is a safety matter",
		)

		# With a passing number beside the marker: safety outranks a correct value.
		numbered = _submit(ACTIVITY_1, "72.5 and stop the medicine")
		_assert(
			numbered["outcome"] == "Fail" and numbered["critical_error"] == 1,
			f"A correct number beside a critical marker was scored on the number: {numbered}",
		)
	finally:
		_author(ACTIVITY_1, **original)
		_reset_competency()


# ------------------------------------------------------- phase 3: rubric evaluator
def check_rubric_aggregates_to_partial():
	"""All met is a Pass, none a Fail, anything between a Partial; a criterion that does not exist is refused."""
	from sparsh_los import review

	_make_learner(TEST_LEARNER)
	_reset_competency()
	original = _author(ACTIVITY_2, evaluation_mode="Rubric", rubric_criteria=RUBRIC_CRITERIA)
	criteria = [line for line in RUBRIC_CRITERIA.splitlines() if line.strip()]
	try:
		attempts = [
			_submit(ACTIVITY_2, f"zzv rubric answer {i}", as_user=TEST_LEARNER)["attempt"] for i in range(4)
		]
		frappe.db.commit()
		for name in attempts:
			_assert(
				frappe.db.get_value("Sparsh Attempt", name, "outcome") == "Not Evaluated",
				"A Rubric attempt was scored at submission",
			)

		partial = review.score_rubric(attempts[0], [1, 3])
		full = review.score_rubric(attempts[1], "[1, 2, 3]")
		none = review.score_rubric(attempts[2], [])
		frappe.db.commit()
		_assert(partial["outcome"] == "Partial", f"Two of three met aggregated to {partial['outcome']}")
		_assert(full["outcome"] == "Pass", f"Three of three met aggregated to {full['outcome']}")
		_assert(none["outcome"] == "Fail", f"None met aggregated to {none['outcome']}")
		_assert(
			partial["criteria_met"] == [1, 3] and partial["criteria_total"] == 3,
			f"The verdict does not report which criteria were met: {partial}",
		)
		_raises(
			lambda: review.score_rubric(attempts[3], [4]),
			"A criterion beyond the rubric was accepted",
			expect="does not exist",
		)
		_assert(
			not frappe.db.exists("Sparsh Evidence", {"attempt": attempts[3]}),
			"The refused verdict left evidence behind",
		)

		for verdict, attempt in ((partial, attempts[0]), (full, attempts[1]), (none, attempts[2])):
			evidence = frappe.get_doc("Sparsh Evidence", verdict["evidence"])
			recorded = frappe.db.get_value("Sparsh Attempt", attempt, "hint_level_used")
			_assert(
				evidence.assistance_level == recorded,
				f"Evidence assistance {evidence.assistance_level} differs from the attempt's {recorded}",
			)
			_assert(evidence.outcome == verdict["outcome"], "The evidence outcome differs from the verdict")
			_assert(evidence.human_review_status == "Approved", "A rubric verdict is not marked as a person's review")
			_assert(evidence.attempt == attempt, "The evidence does not cite the attempt it judged")

		summary = frappe.db.get_value("Sparsh Evidence", partial["evidence"], "ai_feedback_summary") or ""
		_assert("2 of 3" in summary, f"The summary does not say how many criteria were met: {summary!r}")
		_assert("(1, 3)" in summary, f"The summary does not say which criteria were met: {summary!r}")
		for line in criteria:
			_assert(line not in summary, f"Criterion text reached the learner-readable summary: {summary!r}")
		_assert(
			full["state"] == DEMONSTRATED,
			f"An unaided rubric Pass did not demonstrate the competency: {full['state']}",
		)
	finally:
		_author(ACTIVITY_2, **original)
		_reset_competency()


def check_rubric_verdict_issues_a_hint_and_counts_as_assistance():
	"""A Partial from a person is a failure the ladder counts: `start` hands back the hint, the next answer is assisted."""
	from sparsh_los import review, runner

	_make_learner(TEST_LEARNER)
	_reset_competency()
	original = _author(ACTIVITY_2, evaluation_mode="Rubric", rubric_criteria=RUBRIC_CRITERIA)
	try:
		first = _submit(ACTIVITY_2, "zzv first rubric answer", as_user=TEST_LEARNER)
		frappe.db.commit()
		_assert(
			frappe.db.get_value("Sparsh Attempt", first["attempt"], "hint_level_used") == 0,
			"The first attempt was already assisted, so the verdict cannot be shown to add assistance",
		)

		verdict = review.score_rubric(first["attempt"], [1])
		frappe.db.commit()
		_assert(verdict["outcome"] == "Partial", f"The fixture verdict is {verdict['outcome']}")
		_assert(
			verdict.get("hint_level") == 1 and verdict.get("hint") == "First hint.",
			f"A Partial verdict did not issue the first hint: {verdict}",
		)
		_assert(verdict.get("can_retry") is True, "A retry was not offered after a Partial")

		original_user = frappe.session.user
		try:
			frappe.set_user(TEST_LEARNER)
			opened = runner.start(ACTIVITY_2)
		finally:
			frappe.set_user(original_user)
		_assert(
			opened["hint_level"] == 1 and opened["hint"] == verdict["hint"],
			f"start() did not hand the reviewer's hint to the learner: {opened}",
		)
		_assert(opened["retry_index"] == 1, f"start() does not report the one attempt already made: {opened}")

		second = _submit(ACTIVITY_2, "zzv second rubric answer", as_user=TEST_LEARNER)
		frappe.db.commit()
		_assert(
			frappe.db.get_value("Sparsh Attempt", second["attempt"], "hint_level_used") == 1,
			"The answer after a reviewer's hint was recorded as unaided",
		)
		_assert(second["hint_level"] == 1 and second["retry_index"] == 1, f"The runner reported another position: {second}")
		_assert(
			frappe.db.exists(
				"Sparsh Event",
				{"event_type": "hint_shown", "learner": TEST_LEARNER, "activity": ACTIVITY_2, "detail": "level=1 via=review"},
			),
			"The hint a verdict issued was not logged",
		)
	finally:
		_author(ACTIVITY_2, **original)
		_reset_competency()


def check_rubric_refuses_self_review_and_wrong_mode():
	"""Nobody scores their own rubric, and a rubric verdict lands only on a Rubric activity."""
	from sparsh_los import review

	dual = _make_learner(DUAL_LEARNER)
	if "Sparsh Reviewer" not in [r.role for r in dual.roles]:
		dual.append("roles", {"role": "Sparsh Reviewer"})
		dual.save(ignore_permissions=True)
	_make_learner(TEST_LEARNER)
	_reset_competency()
	original_two = _author(ACTIVITY_2, evaluation_mode="Rubric", rubric_criteria=RUBRIC_CRITERIA)
	# Criteria on the Deterministic activity too, so the mode test is the only thing
	# between it and a verdict -- an empty rubric would refuse for another reason.
	original_one = _author(
		ACTIVITY_1, evaluation_mode="Deterministic", expected_response=None, rubric_criteria=RUBRIC_CRITERIA
	)
	_ensure_activity(REFLECTION_ACTIVITY, evaluation_mode="Reflection", rubric_criteria=RUBRIC_CRITERIA)
	try:
		own = _submit(ACTIVITY_2, "zzv my own rubric work", as_user=DUAL_LEARNER)
		frappe.db.commit()
		original_user = frappe.session.user
		try:
			frappe.set_user(DUAL_LEARNER)
			_refused(
				lambda: review.score_rubric(own["attempt"], [1, 2, 3]),
				"A learner-reviewer scored their own rubric attempt",
				expect="your own attempt",
			)
		finally:
			frappe.set_user(original_user)
		_assert(not frappe.db.exists("Sparsh Evidence", {"attempt": own["attempt"]}), "Self-scoring left evidence")

		deterministic = _submit(ACTIVITY_1, "zzv an answer with no key to match", as_user=TEST_LEARNER)
		frappe.db.commit()
		_assert(deterministic["outcome"] == "Not Evaluated", "The Deterministic control attempt was scored")
		_raises(
			lambda: review.score_rubric(deterministic["attempt"], [1, 2, 3]),
			"A rubric verdict was written on a Deterministic activity",
			expect="applies only to a rubric activity",
		)

		reflection = _submit(REFLECTION_ACTIVITY, "zzv a private reflection", as_user=TEST_LEARNER)
		frappe.db.commit()
		_raises(
			lambda: review.score_rubric(reflection["attempt"], [1, 2, 3]),
			"A rubric verdict was written on a Reflection",
			expect="not turned into evidence",
		)
		for attempt in (deterministic["attempt"], reflection["attempt"]):
			_assert(not frappe.db.exists("Sparsh Evidence", {"attempt": attempt}), "A refused verdict left evidence")
	finally:
		_author(ACTIVITY_2, **original_two)
		_author(ACTIVITY_1, **original_one)
		_delete_all("Sparsh Attempt", {"activity": REFLECTION_ACTIVITY})
		_delete_all("Sparsh Activity", {"name": REFLECTION_ACTIVITY})
		_reset_competency()


def check_rubric_is_never_auto_scored():
	"""Rubric is outside the allow-list, so an answer matching the key is still Not Evaluated."""
	from sparsh_los import runner

	_reset_competency()
	original = _author(
		ACTIVITY_2, evaluation_mode="Rubric", rubric_criteria=RUBRIC_CRITERIA, expected_response="level two"
	)
	try:
		_assert(runner.RUBRIC_MODE == "Rubric", f"RUBRIC_MODE is {runner.RUBRIC_MODE!r}")
		_assert(
			"Rubric" not in runner.AUTO_SCORED_MODES,
			f"Rubric is in AUTO_SCORED_MODES: {runner.AUTO_SCORED_MODES}",
		)
		outcome, critical = runner.evaluate(frappe.get_doc("Sparsh Activity", ACTIVITY_2), "level two")
		_assert(
			(outcome, critical) == ("Not Evaluated", 0),
			f"A Rubric activity scored an answer matching its key: {outcome}/{critical}",
		)
		result = _submit(ACTIVITY_2, "level two")
		_assert(result["outcome"] == "Not Evaluated" and "evidence" not in result, f"The runner scored a Rubric attempt: {result}")
		_assert("reviewed by a person" in result["message"].lower(), f"The learner was told: {result['message']}")
	finally:
		_author(ACTIVITY_2, **original)
		_reset_competency()


def check_learner_cannot_read_rubric_or_numeric_key():
	"""The rubric and the numeric key sit at permlevel 1, behind a DocType a learner cannot list at all."""
	_make_learner(TEST_LEARNER)
	original = _author(ACTIVITY_2, expected_value="72.5", rubric_criteria=RUBRIC_CRITERIA)
	meta = frappe.get_meta("Sparsh Activity")
	try:
		for field in ("rubric_criteria", "expected_value", "tolerance"):
			_assert(
				meta.get_field(field).permlevel == 1,
				f"{field} is at permlevel {meta.get_field(field).permlevel}: an answer key at the document's own level",
			)

		original_user = frappe.session.user
		try:
			frappe.set_user(TEST_LEARNER)
			for field, pattern in (("rubric_criteria", "Names%"), ("expected_value", "72%")):
				_refused(
					lambda: frappe.client.get_list(
						"Sparsh Activity", filters={field: ("like", pattern)}, fields=["name"], limit_page_length=1
					),
					f"A learner could filter Activity on {field}",
					expect="insufficient permission",
				)
			levels = meta.get_permlevel_access("read", user=TEST_LEARNER)
			_assert(1 not in levels, f"A learner holds permlevel-1 read on Activity: {levels}")

			doc = frappe.get_doc("Sparsh Activity", ACTIVITY_2)
			doc.apply_fieldlevel_read_permissions()
			_assert(not doc.get("rubric_criteria"), "A learner could read the rubric criteria")
			_assert(not doc.get("expected_value"), "A learner could read the numeric key")
		finally:
			frappe.set_user(original_user)
	finally:
		_author(ACTIVITY_2, **original)


def check_ai_assisted_awaits_implementation():
	"""The one model-backed mode is declared, dispatched by nothing, and never falls through to the key."""
	from sparsh_los import dashboard, review, runner

	_assert(
		runner.AWAITING_IMPLEMENTATION_MODES == ("AI-assisted",),
		f"AWAITING_IMPLEMENTATION_MODES is {runner.AWAITING_IMPLEMENTATION_MODES}",
	)
	_assert("AI-assisted" not in runner.AUTO_SCORED_MODES, "AI-assisted is auto-scored")
	_assert(dashboard.MODEL_MODES == ("AI-assisted",), f"MODEL_MODES is {dashboard.MODEL_MODES}")

	_reset_competency()
	_link_rule("Validated")
	original = _author(ACTIVITY_1, evaluation_mode="AI-assisted", expected_response="level two")
	try:
		outcome, critical = runner.evaluate(frappe.get_doc("Sparsh Activity", ACTIVITY_1), "level two")
		_assert(
			(outcome, critical) == ("Not Evaluated", 0),
			f"AI-assisted under a Validated rule scored a matching answer: {outcome}/{critical}",
		)
		result = _submit(ACTIVITY_1, "level two")
		frappe.db.commit()
		_assert(result["outcome"] == "Not Evaluated" and "evidence" not in result, f"The runner scored it: {result}")
		_assert(
			result["attempt"] in {w["name"] for w in review.pending(competency=COMPETENCY)},
			"An AI-assisted attempt is not waiting for a person",
		)

		# Positive control under the same Validated rule.
		_author(ACTIVITY_1, evaluation_mode="Deterministic")
		scored = _submit(ACTIVITY_1, "level two")
		_assert(scored["outcome"] == "Pass", f"The Deterministic control did not score: {scored['outcome']}")
	finally:
		_author(ACTIVITY_1, **original)
		_reset_competency()


# ------------------------------------------ phase 3: certification and compliance
def _certify(learner, renewal_due=None, competency=COMPETENCY, version=None):
	doc = frappe.new_doc("Sparsh Certification Record")
	doc.learner = learner
	doc.competency = competency
	doc.certification_status = "Full"
	if renewal_due is not None:
		doc.renewal_due = renewal_due
	if version is not None:
		doc.certificate_version = version
	doc.insert(ignore_permissions=True)
	doc.submit()
	frappe.db.commit()
	return doc


def _revoke(certificate):
	doc = frappe.new_doc("Sparsh Certification Record")
	doc.learner = certificate.learner
	doc.competency = certificate.competency
	doc.certification_status = "Revoked"
	doc.revokes = certificate.name
	doc.insert(ignore_permissions=True)
	doc.submit()
	frappe.db.commit()
	return doc


def _stored_version(name):
	return frappe.db.get_value("Sparsh Certification Record", name, "certificate_version")


def check_certificate_version_increments_across_revocation():
	"""The first certificate is v1, the one after a revocation v2, and a revocation has no version."""
	from sparsh_los import certification

	_reset_competency()
	_new_evidence(ACTIVITY_1, "Pass")
	first = _certify(LEARNER)
	_assert(_stored_version(first.name) == 1, f"The first certificate is version {_stored_version(first.name)}")

	revocation = _revoke(first)
	# Crafted at 7: the ordinal is assigned at submission, whatever the draft carried.
	second = _certify(LEARNER, version=7)
	_assert(_stored_version(second.name) == 2, f"The certificate after a revocation is version {_stored_version(second.name)}")

	history = {row["name"]: row for row in certification.readiness(COMPETENCY, LEARNER)["certifications"]}
	for name in (first.name, revocation.name, second.name):
		_assert(name in history, f"{name} is missing from the learner's certification history")
	_assert(history[first.name]["certificate_version"] == 1, f"History reports v{history[first.name]['certificate_version']} for the first")
	_assert(history[second.name]["certificate_version"] == 2, f"History reports v{history[second.name]['certificate_version']} for the second")
	_assert(
		history[revocation.name]["certificate_version"] is None,
		f"A revocation record carries a version: {history[revocation.name]['certificate_version']}",
	)
	_assert(
		certification.certificate_detail(second.name)["certificate_version"] == 2,
		"certificate_detail reports another version for the second certificate",
	)
	_assert(
		certification.certificate_detail(revocation.name)["certificate_version"] is None,
		"certificate_detail gives a revocation a version",
	)
	frappe.db.commit()


def check_next_version_places_after_unnumbered_legacy():
	"""Records that predate the column hold 0; a new certificate is still placed after all of them."""
	from sparsh_los.sparsh_los.doctype.sparsh_certification_record.sparsh_certification_record import (
		_next_version,
	)

	_reset_competency()
	_new_evidence(ACTIVITY_1, "Pass")
	first = _certify(LEARNER)
	_revoke(first)
	second = _certify(LEARNER)
	_revoke(second)
	frappe.db.sql(
		"update `tabSparsh Certification Record` set certificate_version = 0 where name in (%s, %s)",
		(first.name, second.name),
	)
	frappe.db.commit()
	_assert(
		_stored_version(first.name) == 0 and _stored_version(second.name) == 0,
		"The fixture rows are still numbered, so there is nothing legacy to place after",
	)

	_assert(
		_next_version(LEARNER, COMPETENCY, "not-a-record") == 3,
		f"After two unnumbered issue records the next version is {_next_version(LEARNER, COMPETENCY, 'not-a-record')}, not 3",
	)
	third = _certify(LEARNER)
	_assert(_stored_version(third.name) == 3, f"The third certificate was issued as version {_stored_version(third.name)}")

	# A legacy row numbered above the count still wins: the sequence never reuses a number.
	frappe.db.sql("update `tabSparsh Certification Record` set certificate_version = 5 where name = %s", first.name)
	frappe.db.commit()
	_assert(
		_next_version(LEARNER, COMPETENCY, "not-a-record") == 6,
		f"With a legacy v5 on file the next version is {_next_version(LEARNER, COMPETENCY, 'not-a-record')}, not 6",
	)
	frappe.db.commit()


def check_backfill_certificate_versions():
	"""The patch numbers a lone legacy certificate 1 and refuses to order a pair, naming both."""
	import contextlib
	import io

	from sparsh_los.patches.v1_0 import backfill_certificate_versions as patch

	_make_learner(TEST_LEARNER)
	_reset_competency()
	_new_evidence(ACTIVITY_1, "Pass")
	_new_evidence(ACTIVITY_1, "Pass", learner=TEST_LEARNER)
	lone = _certify(LEARNER)
	older = _certify(TEST_LEARNER)
	revocation = _revoke(older)
	newer = _certify(TEST_LEARNER)
	frappe.db.sql(
		"update `tabSparsh Certification Record` set certificate_version = 0 where name in (%s, %s, %s)",
		(lone.name, older.name, newer.name),
	)
	frappe.db.commit()
	_assert(
		all(_stored_version(n) == 0 for n in (lone.name, older.name, newer.name)),
		"The fixture rows are still numbered; the patch has nothing to do",
	)

	report = io.StringIO()
	with contextlib.redirect_stdout(report):
		patch.execute()
	frappe.db.commit()
	report = report.getvalue()

	_assert(_stored_version(lone.name) == 1, f"The patch left the lone legacy certificate at {_stored_version(lone.name)}")
	_assert(
		_stored_version(older.name) == 0 and _stored_version(newer.name) == 0,
		f"The patch ordered a pair it cannot order: {_stored_version(older.name)}, {_stored_version(newer.name)}",
	)
	_assert(
		older.name in report and newer.name in report and "undecided" in report,
		f"The patch did not report the undecided pair by name: {report!r}",
	)
	_assert(TEST_LEARNER not in report and LEARNER not in report, f"The patch printed a learner id: {report!r}")
	_assert(_stored_version(revocation.name) in (0, None), "The patch numbered a revocation record")

	with contextlib.redirect_stdout(io.StringIO()):
		patch.execute()
	frappe.db.commit()
	_assert(_stored_version(lone.name) == 1, "A second run disturbed the numbered certificate")
	_assert(
		_stored_version(older.name) == 0 and _stored_version(newer.name) == 0,
		"A second run numbered a row the first run declined",
	)


def _compliance_rows(**kwargs):
	from sparsh_los import certification

	view = certification.compliance_view(**kwargs)
	return view, {row["learner"]: row for row in view["rows"]}


def check_compliance_view_buckets():
	"""Pending sign-off, Certified, Renewal due, Not yet ready -- and a safety block outranks a lapsed renewal."""
	from sparsh_los import certification

	_reset_competency()
	_, rows = _compliance_rows(competency=COMPETENCY)
	_assert(LEARNER not in rows, "A learner with no state and no certificate is in the compliance view")

	_new_evidence(ACTIVITY_1, "Pass")
	view, rows = _compliance_rows(competency=COMPETENCY)
	row = rows.get(LEARNER)
	_assert(row and row["compliance_status"] == certification.PENDING_SIGN_OFF, f"Demonstrated and uncertified reads as {row}")
	_assert(row["verdict"] == certification.READY and row["sign_off"] is None, f"Pending sign-off carries another verdict: {row}")
	month = frappe.utils.getdate().strftime("%Y-%m")
	issued_before = view["by_period"].get(month, {}).get("issued", 0)

	certificate = _certify(LEARNER, renewal_due=frappe.utils.add_days(frappe.utils.getdate(), 30))
	view, rows = _compliance_rows(competency=COMPETENCY)
	row = rows[LEARNER]
	_assert(row["compliance_status"] == certification.CERTIFIED, f"A standing certificate reads as {row['compliance_status']}")
	_assert(
		row["sign_off"]["certificate"] == certificate.name
		and row["sign_off"]["certificate_version"] == 1
		and row["sign_off"]["version_known"] is True,
		f"The sign-off does not describe the certificate: {row['sign_off']}",
	)
	_assert(
		row["renewal_policy"] == str(certificate.renewal_due),
		f"A set renewal date is not reported as the policy: {row['renewal_policy']!r}",
	)
	_assert(
		view["by_period"].get(month, {}).get("issued", 0) - issued_before == 1,
		f"The certificate was not counted in its month: {view['by_period']}",
	)

	yesterday = frappe.utils.add_days(frappe.utils.getdate(), -1)
	frappe.db.set_value("Sparsh Certification Record", certificate.name, "renewal_due", yesterday)
	frappe.db.commit()
	_, rows = _compliance_rows(competency=COMPETENCY)
	row = rows[LEARNER]
	_assert(row["compliance_status"] == certification.RENEWAL_DUE, f"A lapsed renewal date reads as {row['compliance_status']}")
	_assert(frappe.utils.getdate(row["renewal_due"]) == yesterday, f"The row does not carry the renewal date: {row['renewal_due']}")

	_new_evidence(ACTIVITY_2, "Fail", critical_error=1)
	view, rows = _compliance_rows(competency=COMPETENCY)
	row = rows[LEARNER]
	_assert(row["verdict"] == certification.BLOCKED, f"The fixture did not block: {row['verdict']}")
	_assert(
		row["compliance_status"] == certification.NOT_YET_READY,
		f"A blocked learner with a lapsed renewal reads as {row['compliance_status']!r}; a safety block must "
		"never read as merely 'Renewal due'",
	)
	_assert(row["blocking_evidence"], "The blocking evidence is not named on the compliance row")
	_assert(
		row["sign_off"]["certification_state"] == "Suspended",
		f"The certificate was not suspended under the block: {row['sign_off']}",
	)
	tally = {bucket: 0 for bucket in certification.COMPLIANCE_BUCKETS}
	for each in view["rows"]:
		tally[each["compliance_status"]] += 1
	_assert(view["counts"] == tally, f"counts {view['counts']} do not tally the rows {tally}")
	frappe.db.commit()


def check_compliance_view_reuses_readiness():
	"""Every row's verdict and reason are readiness()'s own, verbatim."""
	from sparsh_los import certification

	_make_learner(TEST_LEARNER)
	_reset_competency()
	_new_evidence(ACTIVITY_1, "Pass")
	_new_evidence(ACTIVITY_1, "Pass", learner=TEST_LEARNER)
	_new_evidence(ACTIVITY_2, "Fail", critical_error=1, learner=TEST_LEARNER)
	frappe.db.commit()

	view, rows = _compliance_rows(competency=COMPETENCY)
	_assert(LEARNER in rows and TEST_LEARNER in rows, f"The fixture learners are not both in the view: {sorted(rows)}")
	for row in view["rows"]:
		expected = certification.readiness(row["competency"], row["learner"])
		for key in ("verdict", "reason", "state"):
			_assert(
				row[key] == expected[key],
				f"{row['learner']}: compliance {key} {row[key]!r} differs from readiness {expected[key]!r}",
			)
	_assert(
		rows[LEARNER]["reason"] != rows[TEST_LEARNER]["reason"],
		"The two fixture rows share one reason, so a literal could satisfy both",
	)
	frappe.db.commit()


def check_compliance_renewal_null_reads_as_no_policy():
	"""No renewal date means no policy has been set -- not that renewal is due today."""
	from sparsh_los import certification

	_reset_competency()
	_assert(
		not frappe.get_meta("Sparsh Certification Record").get_field("renewal_due").default,
		"renewal_due carries a default, so every certificate is born with a renewal policy nobody set",
	)
	_new_evidence(ACTIVITY_1, "Pass")
	certificate = _certify(LEARNER)
	_assert(
		frappe.db.get_value("Sparsh Certification Record", certificate.name, "renewal_due") is None,
		"A certificate issued with no renewal date was stored with one",
	)
	_, rows = _compliance_rows(competency=COMPETENCY)
	row = rows[LEARNER]
	_assert(row["renewal_due"] is None, f"renewal_due is {row['renewal_due']!r}")
	_assert(
		row["renewal_policy"] == certification.NO_RENEWAL_POLICY,
		f"renewal_policy is {row['renewal_policy']!r}, not {certification.NO_RENEWAL_POLICY!r}",
	)
	_assert(
		row["compliance_status"] == certification.CERTIFIED,
		f"A certificate with no renewal policy reads as {row['compliance_status']!r}",
	)
	frappe.db.commit()


def check_compliance_view_is_reviewer_gated():
	from sparsh_los import certification

	_make_learner(TEST_LEARNER)
	frappe.db.commit()
	original_user = frappe.session.user
	try:
		frappe.set_user(TEST_LEARNER)
		_refused(lambda: certification.compliance_view(), "A learner read the compliance view", expect="reviewer action")
		_refused(
			lambda: certification.compliance_view(competency=COMPETENCY),
			"A learner read the compliance view for a competency",
			expect="reviewer action",
		)
	finally:
		frappe.set_user(original_user)
	_raises(
		lambda: certification.compliance_view(competency=PREFIX + "NOPE"),
		"An unknown competency was reported on rather than refused",
		expect="no such competency",
	)


def check_compliance_cohort_rate_counts_non_starters():
	"""A cohort member with no evidence is in the denominator: two members, one certified, rate 0.5."""
	from sparsh_los import certification

	_make_learner(TEST_LEARNER)
	_make_learner(OTHER_LEARNER)
	_reset_competency()
	_clear_cohorts()
	try:
		_new_evidence(ACTIVITY_1, "Pass", learner=TEST_LEARNER)
		_certify(TEST_LEARNER)
		_new_cohort(COHORT_A, [TEST_LEARNER, OTHER_LEARNER], status="Active")
		frappe.db.commit()
		_assert(
			not frappe.db.exists("Sparsh Mastery State", {"learner": OTHER_LEARNER, "competency": COMPETENCY}),
			"The non-starter already has a state, so the cohort loop is not what includes them",
		)

		view, rows = _compliance_rows(competency=COMPETENCY, cohort=COHORT_A)
		_assert(set(rows) == {TEST_LEARNER, OTHER_LEARNER}, f"The cohort view covers {sorted(rows)}")
		_assert(
			rows[OTHER_LEARNER]["compliance_status"] == certification.NOT_YET_READY
			and rows[OTHER_LEARNER]["state"] == "Not Started",
			f"The non-starter is misreported: {rows[OTHER_LEARNER]}",
		)
		_assert(rows[TEST_LEARNER]["compliance_status"] == certification.CERTIFIED, "The certified member is not Certified")
		cohort = view["by_cohort"].get(COHORT_A)
		_assert(cohort, f"The cohort has no aggregate row: {view['by_cohort']}")
		_assert(cohort["pairs"] == 2, f"The cohort denominator is {cohort['pairs']}, not 2")
		_assert(cohort[certification.CERTIFIED] == 1, f"The cohort counts {cohort[certification.CERTIFIED]} certified")
		_assert(cohort["certified_rate"] == 0.5, f"The certified rate is {cohort['certified_rate']}, not 0.5")
		_assert(view["counts"][certification.CERTIFIED] == 1 and view["counts"][certification.NOT_YET_READY] == 1, f"counts: {view['counts']}")
	finally:
		_clear_cohorts()
		_reset_competency()


# --------------------------------------------------------------- phase 3: source
LIFECYCLE_HOOKS = {
	"validate",
	"before_insert",
	"before_save",
	"before_submit",
	"on_update",
	"on_update_after_submit",
	"on_submit",
	"on_cancel",
	"on_trash",
	"after_insert",
	"after_delete",
}


def check_controller_hooks_are_on_the_class():
	"""Every lifecycle hook in a controller module is a method of its Document class.

	A method pasted at the wrong indentation, or a module-level helper inserted between
	two methods, silently turns every method after it into a module-level or nested
	function. `ast.parse` accepts the file, the import succeeds, and the DocType simply
	stops running those hooks -- twice here: `on_update` on the rule controller, and
	`on_update_after_submit`/`on_cancel`/`on_submit` on the certification record.
	Two shapes are flagged: a hook name defined anywhere other than directly in a class
	body, and a function taking `self` that is not directly in a class body.
	"""
	import ast
	import pathlib

	root = pathlib.Path(frappe.get_app_path("sparsh_los")) / "sparsh_los" / "doctype"
	offenders = []
	controllers = 0
	hooks_on_classes = 0

	def is_document_base(base):
		return (isinstance(base, ast.Name) and base.id == "Document") or (
			isinstance(base, ast.Attribute) and base.attr == "Document"
		)

	def visit(node, parent, path, tally):
		"""Walk with the parent in hand, so "directly in a class body" is decidable."""
		for child in ast.iter_child_nodes(node):
			if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
				in_class = isinstance(parent, ast.ClassDef)
				takes_self = bool(child.args.args) and child.args.args[0].arg == "self"
				if in_class and child.name in LIFECYCLE_HOOKS:
					tally.append(child.name)
				if not in_class and (child.name in LIFECYCLE_HOOKS or takes_self):
					where = (
						"module level"
						if isinstance(parent, ast.Module)
						else f"nested in {getattr(parent, 'name', type(parent).__name__)}()"
					)
					offenders.append(f"{path.relative_to(root.parent.parent)}:{child.lineno} {child.name} at {where}")
			visit(child, child, path, tally)

	for path in sorted(root.rglob("*.py")):
		if "__pycache__" in path.parts or path.name.startswith("._") or path.name == "__init__.py":
			continue
		tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
		if not any(
			isinstance(node, ast.ClassDef) and any(is_document_base(b) for b in node.bases) for node in tree.body
		):
			continue
		controllers += 1
		tally = []
		visit(tree, tree, path, tally)
		hooks_on_classes += len(tally)

	_assert(controllers >= 10, f"Only {controllers} controller module(s) were scanned; the scan is not finding them")
	_assert(hooks_on_classes >= 20, f"Only {hooks_on_classes} lifecycle hooks were found on classes; the scan is not finding methods")
	_assert(
		not offenders,
		"Lifecycle hook or method defined outside its Document class -- the DocType has silently "
		f"stopped running it: {'; '.join(offenders)}",
	)


def check_validated_means_somebody_validated_it():
	"""Validated is the switch that lets the engine score automatically, so it is guarded.

	Nothing guarded the transition: any writer could set `status = "Validated"` on a rule
	still carrying the *candidate* wording, with no owner and no date, and the engine
	would then treat unapproved text as programme policy. The candidate and the approved
	wording are separate fields so a reader can always see what changed between what was
	proposed and what was decided.
	"""
	_delete_all("Sparsh Source of Truth Rule", {"rule_id": PREFIX + "VAL"})
	frappe.db.commit()

	def _rule(**values):
		rule = frappe.new_doc("Sparsh Source of Truth Rule")
		rule.rule_id = PREFIX + "VAL"
		rule.version = 1
		rule.rule_statement = "A candidate somebody proposed."
		for field, value in values.items():
			rule.set(field, value)
		rule.insert(ignore_permissions=True)
		return rule

	try:
		complete = {
			"approved_statement": "The owner's wording.",
			"approved_by_name": "A Named Programme Owner",
			"approval_source": "The governance record that carries the approval.",
			"effective_date": frappe.utils.today(),
		}

		# Each requirement missing in turn, and the message must name what is missing.
		for missing, field in (
			("the approved wording", "approved_statement"),
			("the name of the person who approved it", "approved_by_name"),
			("the document the approval comes from", "approval_source"),
			("an effective date", "effective_date"),
		):
			values = {k: v for k, v in complete.items() if k != field}
			_raises(
				lambda v=values: _rule(status="Validated", **v),
				f"A rule was Validated without {missing}",
				expect=missing,
			)

		# `rule_owner` is deliberately NOT required. Requiring it made "approved this
		# clinically" depend on "holds a Frappe account", which blocked the first real
		# decision the guard ever met -- the programme owner signed a matrix and has no
		# login. Asserting its absence is allowed keeps anyone from reinstating it.
		rule = _rule(status="Validated", **complete)
		_assert(
			not rule.rule_owner,
			"The fixture set a rule owner, so this does not prove one is unnecessary",
		)
		frappe.db.commit()
		stored = frappe.db.get_value(
			"Sparsh Source of Truth Rule", rule.name,
			["rule_statement", "approved_statement"], as_dict=True,
		)
		_assert(
			stored.rule_statement == "A candidate somebody proposed.",
			"Approving the rule overwrote the candidate, so what changed cannot be seen",
		)
		_assert(
			stored.approved_statement == "The owner's wording.",
			"The approved wording was not stored",
		)
		# Who transcribed the approval is taken from the session, never the payload --
		# the same rule that closed the self-answered escalation.
		_assert(
			frappe.db.get_value("Sparsh Source of Truth Rule", rule.name, "approval_recorded_by")
			== frappe.session.user,
			"The approval was not attributed to the session that recorded it",
		)
	finally:
		_delete_all("Sparsh Source of Truth Rule", {"rule_id": PREFIX + "VAL"})
		frappe.db.commit()


def check_owner_decisions_reach_validated():
	"""A row his matrix marks Validated arrives Validated, with its provenance attached.

	The loader used to leave everything Draft because `rule_owner` was required and he
	holds no account here. That made the engine unable to act on a decision he had
	actually signed -- the guard was refusing the very thing it was built to permit.
	Authority now rests on the governance record: who approved it, which document says
	so, and when it took effect.
	"""
	from sparsh_los import seed

	rows = {row["rule_id"]: row for row in seed._rows()}
	signed = [
		rule_id for rule_id, row in rows.items()
		if row.get("source_status") == "Validated" and row.get("approved_statement")
	]
	_assert(signed, "The matrix source carries no signed decision, so nothing is proved here")

	approval = seed._approval()
	_assert(
		approval.get("approved_by_name") and approval.get("approval_source"),
		"The approval record names nobody, so a Validated rule would rest on nothing",
	)

	not_validated, no_provenance = [], []
	for rule_id in signed:
		row = frappe.db.get_value(
			"Sparsh Source of Truth Rule",
			{"rule_id": rule_id, "status": ("!=", "Superseded")},
			["name", "status", "approved_statement", "approved_by_name", "approval_source",
			 "effective_date"],
			as_dict=True,
			order_by="version desc",
		)
		if not row or row.status != "Validated":
			not_validated.append(f"{rule_id}={row.status if row else 'missing'}")
			continue
		if not (row.approved_by_name and row.approval_source and row.effective_date):
			no_provenance.append(rule_id)
		_assert(
			(row.approved_statement or "").strip() == rows[rule_id]["approved_statement"].strip(),
			f"{rule_id} does not carry the programme owner's wording verbatim",
		)

	_assert(
		not not_validated,
		f"Rules he signed are not Validated in the engine: {not_validated}",
	)
	_assert(
		not no_provenance,
		f"Validated without the record it rests on: {no_provenance}",
	)


def check_matrix_keeps_the_owners_own_words():
	"""His three matrix columns are stored verbatim beside the values we map them onto.

	Our Selects cannot hold what his spreadsheet says: the matrix distinguishes "Medium"
	from "Normal" and "High" from "High-risk", and its automation values carry the
	condition under which automation becomes permissible -- "Do not automate *until
	validated*" -- which a four-option Select drops. One row loaded stricter than he had
	authorised with nothing on the record to show it.
	"""
	# The current version of each, not every version: a rule the programme owner has
	# rewritten keeps its superseded predecessor, so an unfiltered read returns two rows
	# for one rule id and the older one still carries the pre-decision wording.
	rows = []
	for rule_id in ("BP-HANDLING-AND-ESCALATION", "70-CONFIDENCE-RULE"):
		found = frappe.get_all(
			"Sparsh Source of Truth Rule",
			filters={"rule_id": rule_id, "status": ("!=", "Superseded")},
			fields=["rule_id", "criticality", "source_criticality", "source_status",
					"source_automation_status"],
			order_by="version desc",
			limit=1,
		)
		rows.extend(found)
	_assert(len(rows) == 2, f"The matrix fixtures are not loaded: {rows}")

	for row in rows:
		_assert(
			(row.source_status or "").strip(),
			f"{row.rule_id} lost the status its matrix row carried",
		)
		_assert(
			(row.source_automation_status or "").strip(),
			f"{row.rule_id} lost the automation wording its matrix row carried",
		)

	confidence = next(r for r in rows if r.rule_id == "70-CONFIDENCE-RULE")
	# The matrix says Medium; our Select has no Medium, so it maps to Normal. Both must
	# be visible or the two documents cannot be reconciled by eye.
	_assert(
		confidence.criticality == "Normal" and confidence.source_criticality == "Medium",
		f"The mapped and original criticality are not both recorded: "
		f"{confidence.criticality} / {confidence.source_criticality}",
	)


def check_mastery_threshold_is_the_owners_not_the_engines():
	"""How many activities make Mastered is a programme decision, and was hardcoded.

	Section 16 asks for thresholds calibrated from what certified volunteers actually
	do, not copied from an assumed number. Two remains the default because it is what
	the engine has always done and nobody has calibrated it -- but a competency the
	programme owner has ruled on must carry their number, and the default must not
	quietly override it.
	"""
	_reset_competency()
	_make_learner(TEST_LEARNER)
	original = frappe.db.get_value("Sparsh Competency", COMPETENCY, "activities_for_mastery")
	try:
		# Three required: two distinct unaided passes must no longer reach Mastered.
		frappe.db.set_value("Sparsh Competency", COMPETENCY, "activities_for_mastery", 3)
		frappe.db.commit()

		_new_evidence(ACTIVITY_1, "Pass", learner=TEST_LEARNER)
		_new_evidence(ACTIVITY_2, "Pass", learner=TEST_LEARNER)
		frappe.db.commit()
		_assert(
			_state(TEST_LEARNER) == DEMONSTRATED,
			f"Two activities reached {_state(TEST_LEARNER)} where the competency requires three",
		)

		# And the engine's old default must not be reachable by leaving the field unset
		# in a way that reads as zero.
		frappe.db.set_value("Sparsh Competency", COMPETENCY, "activities_for_mastery", 0)
		frappe.db.commit()
		from sparsh_los.mastery import recompute_mastery

		recompute_mastery(TEST_LEARNER, COMPETENCY)
		_assert(
			_state(TEST_LEARNER) == MASTERED,
			f"An unset threshold did not fall back to the engine default: {_state(TEST_LEARNER)}",
		)
	finally:
		frappe.db.set_value("Sparsh Competency", COMPETENCY, "activities_for_mastery", original)
		frappe.db.commit()
		_reset_competency()


def check_pilot_gate_names_what_is_missing():
	"""“Can we start on Monday” must be answered by what exists, not by a flag.

	A pilot reported ready when it is not is worse than no report: the people who would
	catch the gap are the ones reading the report. Each precondition is therefore
	asserted by removing it and watching the gate name it.
	"""
	from sparsh_los import pilot

	_make_learner(TEST_LEARNER)
	report = pilot.status()

	_assert(
		isinstance(report.get("blocking"), list),
		"The pilot gate did not report what is blocking",
	)
	_assert(
		report["can_begin"] is (not report["blocking"]),
		f"can_begin disagrees with its own blocking list: {report['can_begin']} / {report['blocking']}",
	)

	# A rule carrying the programme owner's wording but no owner is answered, not in
	# force, and the gate must say which of the two it is.
	rules = report["rules"]
	for bucket in ("in_force", "answered_but_not_in_force", "no_decision_yet"):
		_assert(bucket in rules, f"The pilot gate does not report {bucket}")
	for row in rules["answered_but_not_in_force"]:
		_assert(
			row.get("missing"),
			f"{row.get('rule')} is answered and not in force, with no reason given",
		)

	# No enabled reviewer must block, because nobody can judge an attempt without one.
	_assert(
		"reviewers" in report and "enabled" in report["reviewers"],
		"The pilot gate does not report who can actually judge an attempt",
	)
	if not report["reviewers"]["enabled"]:
		_assert(
			any("reviewer role" in b for b in report["blocking"]),
			f"No enabled reviewer, yet the gate did not block: {report['blocking']}",
		)

	# And it is a reviewer action: it names learners and cohorts.
	original = frappe.session.user
	try:
		frappe.set_user(TEST_LEARNER)
		_refused(pilot.status, "A learner read the pilot gate", expect="reviewer")
	finally:
		frappe.set_user(original)


def _close_open_diagnostics(learner):
	for name in frappe.get_all(
		"Sparsh Assessment Event",
		filters={"learner": learner, "purpose": "Diagnostic", "status": "Open"},
		pluck="name",
	):
		frappe.db.set_value("Sparsh Assessment Event", name, "status", "Abandoned",
							update_modified=False)
	frappe.db.commit()


def check_diagnostic_opens_one_activity_per_competency():
	"""A refresher pathway starts with a short diagnostic, not the whole curriculum.

	Section 8.2: "Do not force current certified volunteers through the entire new
	pathway. Begin with a short competency diagnostic." Nothing implemented it, so a
	certified volunteer had no entry point other than the ordinary pathway.
	"""
	from sparsh_los import diagnostic

	_make_learner(TEST_LEARNER)
	_reset_competency()
	_close_open_diagnostics(TEST_LEARNER)

	original = frappe.session.user
	try:
		frappe.set_user(TEST_LEARNER)
		opened = diagnostic.start()
	finally:
		frappe.set_user(original)

	try:
		offered = opened["activities"]
		competencies = [row["competency"] for row in offered]
		_assert(
			len(competencies) == len(set(competencies)),
			f"The diagnostic offered a competency twice: {competencies}",
		)
		_assert(
			COMPETENCY in competencies,
			f"The fixture competency is not in the diagnostic: {competencies}",
		)
		# Every competency that has an activity, and no competency that has none.
		with_activities = {
			row.competency
			for row in frappe.get_all("Sparsh Activity", fields=["competency"])
			if row.competency
		}
		_assert(
			set(competencies) == with_activities,
			f"The diagnostic covered {sorted(set(competencies))}, not {sorted(with_activities)}",
		)

		# A second start must not open a second diagnostic: two open ones make closing
		# ambiguous and would assign every refresher twice.
		try:
			frappe.set_user(TEST_LEARNER)
			again = diagnostic.start()
		finally:
			frappe.set_user(original)
		_assert(again["already_open"], "A second diagnostic was opened alongside the first")
		_assert(again["event"] == opened["event"], "The second start returned a different event")
	finally:
		_close_open_diagnostics(TEST_LEARNER)


def check_diagnostic_assigns_a_refresher_only_where_weak():
	"""Only weak areas produce a refresher, and the refresher names the gap.

	`PERFORMANCE_GAP` was declared and assigned by nothing, so §8.2's "assign only
	weak-area refreshers" had no implementation at all. Weak means "not an unaided
	pass" -- the assistance model the engine already runs on, not an invented threshold.
	"""
	from sparsh_los import diagnostic, refresher

	_make_learner(TEST_LEARNER)
	_reset_competency()
	_close_open_diagnostics(TEST_LEARNER)
	_delete_all("Sparsh Refresher Assignment", {"learner": TEST_LEARNER})
	_delete_all("Sparsh Refresher Assignment", {"competency": COMPETENCY_2})
	strong_id = PREFIX + "DIAG-OTHER"
	_delete_all("Sparsh Attempt", {"activity": ("like", strong_id + "%")})
	_delete_all("Sparsh Activity", {"activity_id": strong_id})
	other = frappe.new_doc("Sparsh Activity")
	other.activity_id = strong_id
	other.title = "Verification Activity"
	other.competency = COMPETENCY_2
	other.activity_type = "Knowledge check"
	other.instruction = "Verification instruction."
	other.version = 1
	other.evaluation_mode = "Human review"
	other.insert(ignore_permissions=True)
	frappe.db.commit()

	original = frappe.session.user
	try:
		frappe.set_user(TEST_LEARNER)
		opened = diagnostic.start()
	finally:
		frappe.set_user(original)

	try:
		offered = {row["competency"]: row["activity"] for row in opened["activities"]}
		fixture_activity = offered.get(COMPETENCY)
		_assert(fixture_activity, "The fixture competency was not offered, so nothing is proved")

		# A wrong answer on the fixture competency: not an unaided pass.
		_submit(fixture_activity, "zzv a wrong answer entirely", as_user=TEST_LEARNER)
		frappe.db.commit()

		# The positive control, and the whole discriminating power of this check. Without
		# it, a version of `close` that calls every competency weak passes: asserting the
		# failed one is weak says nothing unless a competency the learner demonstrated
		# unaided is shown to produce no refresher. The first version of this check had
		# exactly that hole and survived its own revert.
		strong_activity = offered.get(COMPETENCY_2)
		_assert(
			strong_activity,
			"The second competency was not offered, so the strong case cannot be shown",
		)
		unaided = frappe.new_doc("Sparsh Attempt")
		unaided.learner = TEST_LEARNER
		unaided.activity = strong_activity
		unaided.response = "zzv an unaided correct answer"
		unaided.outcome = "Pass"
		unaided.hint_level_used = 0
		unaided.retry_index = 0
		unaided.attempted_at = frappe.utils.now_datetime()
		unaided.insert(ignore_permissions=True)
		frappe.db.commit()

		result = diagnostic.close(opened["event"])

		_assert(
			COMPETENCY_2 in result["strong"],
			f"An unaided pass was not counted as demonstrated: {result}",
		)
		_assert(
			COMPETENCY_2 not in result["weak"],
			"A competency demonstrated unaided was marked weak",
		)
		_assert(
			not frappe.get_all(
				"Sparsh Refresher Assignment",
				filters={"learner": TEST_LEARNER, "competency": COMPETENCY_2,
						 "status": "Assigned"},
			),
			"A refresher was raised on a competency the learner demonstrated unaided",
		)

		_assert(
			COMPETENCY in result["weak"],
			f"A failed diagnostic did not mark the competency weak: {result}",
		)
		_assert(
			COMPETENCY not in result["not_attempted"],
			"An attempted competency was reported as not attempted",
		)
		# Competencies nobody attempted must be reported as unknown, never as strong.
		for competency in result["not_attempted"]:
			_assert(
				competency not in result["strong"],
				f"{competency} was never attempted and was counted strong",
			)

		assigned = frappe.get_all(
			"Sparsh Refresher Assignment",
			filters={"learner": TEST_LEARNER, "competency": COMPETENCY, "status": "Assigned"},
			fields=["name", "trigger_reason", "focus_activity"],
		)
		_assert(len(assigned) == 1, f"Expected one refresher on the weak competency: {assigned}")
		_assert(
			assigned[0].trigger_reason == refresher.PERFORMANCE_GAP,
			f"The refresher was raised as {assigned[0].trigger_reason}, not a performance gap",
		)
		_assert(
			assigned[0].focus_activity == fixture_activity,
			f"The refresher does not name the activity the gap was found on: {assigned[0]}",
		)

		# A closed diagnostic stays closed: reopening would assign every refresher twice
		# and make the completion time a lie. Asserted on the save path -- the first
		# version of this check called `db_set` first, which bypasses `validate`
		# entirely, so it reopened the event and then found nothing left to refuse. A
		# guard in `validate` is a guard on saving and on nothing else; `db_set` can
		# still reopen one, which is the same limit recorded against every other
		# validate-time guard here.
		_refused(
			lambda: _reopen_event(opened["event"]),
			"A completed diagnostic was reopened",
			expect="cannot be reopened",
		)
	finally:
		_close_open_diagnostics(TEST_LEARNER)
		_delete_all("Sparsh Refresher Assignment", {"learner": TEST_LEARNER})
		_delete_all("Sparsh Attempt", {"activity": ("like", strong_id + "%")})
		_delete_all("Sparsh Activity", {"activity_id": strong_id})
		frappe.db.commit()


def _reopen_event(event):
	doc = frappe.get_doc("Sparsh Assessment Event", event)
	doc.status = "Open"
	doc.save(ignore_permissions=True)


def check_refresher_sends_the_learner_to_the_gap():
	"""A weak-area refresher hands back the activity it was raised on.

	`next_experience` returned `activities[0]` for every refresher regardless of trigger,
	so a learner sent back for one specific failure met whichever activity sorted first
	and could clear the refresher without revisiting what they got wrong.
	"""
	from sparsh_los import orchestrator, refresher

	_make_learner(TEST_LEARNER)
	_reset_competency()
	_delete_all("Sparsh Refresher Assignment", {"learner": TEST_LEARNER})
	frappe.db.commit()

	activities = frappe.get_all(
		"Sparsh Activity", filters={"competency": COMPETENCY},
		fields=["name"], order_by="activity_id asc",
	)
	_assert(len(activities) >= 2,
			"The fixture competency needs two activities for this to prove anything")
	first, second = activities[0]["name"], activities[1]["name"]

	try:
		refresher.assign(TEST_LEARNER, COMPETENCY, refresher.PERFORMANCE_GAP,
						 detail="zzv gap", focus_activity=second)
		frappe.db.commit()

		original = frappe.session.user
		try:
			frappe.set_user(TEST_LEARNER)
			suggestion = orchestrator.next_experience(COMPETENCY)
		finally:
			frappe.set_user(original)

		_assert(
			suggestion["activity"] == second,
			f"The refresher sent the learner to {suggestion['activity']}, not the gap {second}",
		)
		_assert(
			second != first,
			"The gap is the first activity anyway, so this fixture cannot discriminate",
		)
		_assert(not suggestion.get("focus_missing"), "The focus activity was reported missing")
	finally:
		_delete_all("Sparsh Refresher Assignment", {"learner": TEST_LEARNER})
		frappe.db.commit()


def check_assessment_event_cannot_be_deleted():
	"""A checkpoint record is evidence, like an attempt or an event log."""
	_make_learner(TEST_LEARNER)
	event = frappe.new_doc("Sparsh Assessment Event")
	event.learner = TEST_LEARNER
	event.purpose = "Gate"
	event.status = "Open"
	event.insert(ignore_permissions=True)
	frappe.db.commit()

	try:
		_refused(
			lambda: frappe.delete_doc("Sparsh Assessment Event", event.name, force=True,
									  ignore_permissions=True),
			"An assessment event was deleted",
			expect="cannot be deleted",
		)
	finally:
		frappe.flags.in_sparsh_maintenance = True
		try:
			frappe.delete_doc("Sparsh Assessment Event", event.name, force=True,
							  ignore_permissions=True)
		finally:
			frappe.flags.in_sparsh_maintenance = False
		frappe.db.commit()


def check_calibration_reports_the_uncalibrated_as_uncalibrated():
	"""A competency nobody has calibrated must say so.

	`activities_for_mastery` carried a DocType default of 2, so every competency arrived
	holding a number and the calibration report called them all calibrated. A stored 2
	is indistinguishable from a 2 somebody chose, which is exactly what §16 warns
	against. Frappe cannot store an empty Int, so zero is the sentinel.
	"""
	from sparsh_los import calibration
	from sparsh_los.mastery import DEFAULT_ACTIVITIES_FOR_MASTERY

	original = frappe.db.get_value("Sparsh Competency", COMPETENCY,
								   ["activities_for_mastery", "threshold_source"], as_dict=True)
	try:
		frappe.db.set_value("Sparsh Competency", COMPETENCY,
							{"activities_for_mastery": 0, "threshold_source": None},
							update_modified=False)
		frappe.db.commit()

		row = _calibration_row(calibration.observed(competency=COMPETENCY), COMPETENCY)
		_assert(
			row["on_the_uncalibrated_default"],
			"A competency with no threshold set was reported as calibrated",
		)
		_assert(
			row["threshold_in_force"] == DEFAULT_ACTIVITIES_FOR_MASTERY,
			f"The engine's documented default is not what is reported in force: {row}",
		)
		_assert(row["threshold_was_chosen_by"] is None, "A threshold nobody set names a chooser")

		# And a competency somebody did calibrate must not be reported as defaulted.
		frappe.db.set_value("Sparsh Competency", COMPETENCY,
							{"activities_for_mastery": 3,
							 "threshold_source": "zzv a person wrote this down"},
							update_modified=False)
		frappe.db.commit()
		row = _calibration_row(calibration.observed(competency=COMPETENCY), COMPETENCY)
		_assert(
			not row["on_the_uncalibrated_default"],
			"A calibrated competency was reported as sitting on the default",
		)
		_assert(row["threshold_in_force"] == 3, f"The chosen threshold is not in force: {row}")
	finally:
		frappe.db.set_value("Sparsh Competency", COMPETENCY,
							{"activities_for_mastery": original.activities_for_mastery,
							 "threshold_source": original.threshold_source},
							update_modified=False)
		frappe.db.commit()


def _calibration_row(report, competency):
	for row in report["competencies"]:
		if row["competency"] == competency:
			return row
	raise AssertionError(f"{competency} is absent from the calibration report")


def check_calibration_writes_nothing():
	"""The report proposes and never applies.

	A number a script derived and the same script applied has been approved by nobody,
	and would be indistinguishable in the database from one the programme owner chose.
	"""
	from sparsh_los import calibration

	before = {
		row.name: (row.activities_for_mastery, row.threshold_source)
		for row in frappe.get_all(
			"Sparsh Competency", fields=["name", "activities_for_mastery", "threshold_source"]
		)
	}
	calibration.observed()
	after = {
		row.name: (row.activities_for_mastery, row.threshold_source)
		for row in frappe.get_all(
			"Sparsh Competency", fields=["name", "activities_for_mastery", "threshold_source"]
		)
	}
	_assert(before == after, "Running the calibration report changed a threshold")


def check_pilot_prepare_refuses_an_incomplete_roster():
	"""Standing a pilot up needs volunteers and somebody to judge them."""
	from sparsh_los import pilot

	_raises(
		lambda: pilot.prepare(volunteers=[], reviewers=["zzv-rev@example.invalid"]),
		"A pilot was prepared with no volunteers",
		expect="no volunteers were named",
	)
	_raises(
		lambda: pilot.prepare(volunteers=["zzv-vol@example.invalid"], reviewers=[]),
		"A pilot was prepared with nobody to judge it",
		expect="no reviewer was named",
	)
	# One person as both is not refused by the engine's self-judgement guards -- those
	# key on the subject, not the role -- but on a cohort of eight it means the only
	# available reviewer for somebody's work is themselves, and the queue stalls.
	_raises(
		lambda: pilot.prepare(volunteers=["zzv-both@example.invalid"],
							  reviewers=["zzv-both@example.invalid"]),
		"One person was accepted as both volunteer and reviewer",
		expect="both volunteer and reviewer",
	)


def check_pilot_prepare_leaves_activation_to_a_person():
	"""Preparing a pilot must not start one.

	`next_in_pathway` refuses work from a pathway that is not Active, so activation is
	the act that begins the pilot. A script that activated on its own would have started
	a clinical training programme because somebody ran a command.
	"""
	from sparsh_los import cohort, pilot

	volunteers = [f"zzv-pilot{i}@example.invalid" for i in (1, 2)]
	reviewers = ["zzv-pilot-rev@example.invalid"]
	cohort_id = PREFIX + "PILOT"
	original_status = frappe.db.get_value("Sparsh Pathway", "SSP-PILOT", "status")

	try:
		result = pilot.prepare(volunteers=volunteers, reviewers=reviewers, cohort_id=cohort_id)
		_assert(
			result["pathway_status"] != "Active",
			"Preparing the pilot activated the pathway without anybody deciding to begin",
		)
		_assert(
			any("Draft" in line or "Active" in line for line in result["status"]["blocking"]),
			f"The pathway is not Active and nothing said so: {result['status']['blocking']}",
		)
		# The learners are enrolled and assigned even so -- the roster is real, only the
		# start is withheld.
		_assert(
			cohort.pathway_for(volunteers[0]) == "SSP-PILOT",
			"A prepared volunteer is not on the pilot pathway",
		)

		# With the decision taken, nothing else stands in the way.
		result = pilot.prepare(volunteers=volunteers, reviewers=reviewers,
							   cohort_id=cohort_id, activate=1)
		_assert(result["pathway_status"] == "Active", "Activation did not take effect")
		# Asserted on the pathway blocker specifically, not on `can_begin`. This bench
		# carries the harness's own fixture competencies, which have no rules and
		# sometimes no activities, so programme readiness legitimately refuses here for
		# reasons that have nothing to do with the pilot. Asserting `can_begin` would
		# make this check fail for somebody else's fixture.
		remaining = " ".join(result["status"]["blocking"])
		_assert(
			"Draft" not in remaining,
			f"The pathway is Active and still reported Draft: {result['status']['blocking']}",
		)
		_assert(
			"no learner is assigned anything" not in remaining,
			f"The cohort is on an Active pathway and still reported unassigned: "
			f"{result['status']['blocking']}",
		)
	finally:
		frappe.db.set_value("Sparsh Pathway", "SSP-PILOT", "status", original_status,
							update_modified=False)
		name = frappe.db.get_value("Sparsh Cohort", {"cohort_id": cohort_id}, "name")
		if name:
			frappe.delete_doc("Sparsh Cohort", name, force=True, ignore_permissions=True)
		for email in volunteers + reviewers:
			if frappe.db.exists("User", email):
				frappe.delete_doc("User", email, force=True, ignore_permissions=True)
		frappe.db.commit()


def check_decisions_promote_a_rule_whose_wording_arrived_first():
	"""Re-seeding promotes a signed rule that already carries its wording but is Draft.

	`load_matrix_decisions` used to `continue` the moment a rule carried an
	`approved_statement`, on the reasoning that re-running must not rewrite a decision.
	But the wording and the Draft -> Validated promotion were written in the same branch,
	so anything that wrote the wording on its own left the status stranded: no later run
	would ever look at it again. `restore_matrix_source_wording` does exactly that, and on
	the live site seven rules the programme owner had signed sat inert at Draft because of
	it -- the engine will not score against a rule that is not Validated.

	The status is put back afterwards whatever happens, so the check does not leave the
	site holding a decision it invented.
	"""
	from sparsh_los import seed

	rows = {row["rule_id"]: row for row in seed._rows()}
	signed = [
		rule_id for rule_id, row in rows.items()
		if row.get("source_status") == "Validated" and row.get("approved_statement")
	]
	_assert(signed, "The matrix source carries no signed decision, so nothing is proved here")

	rule_id = sorted(signed)[0]
	current = frappe.db.get_value(
		"Sparsh Source of Truth Rule",
		{"rule_id": rule_id, "status": ("!=", "Superseded")},
		["name", "status", "approved_statement", "approved_by_name", "approval_source",
		 "effective_date"],
		as_dict=True,
		order_by="version desc",
	)
	_assert(current, f"{rule_id} is not seeded here, so the promotion cannot be exercised")
	_assert(
		(current.approved_statement or "").strip(),
		f"{rule_id} carries no approved wording, so this is not the state being reproduced",
	)

	was = {
		"status": current.status,
		"approved_by_name": current.approved_by_name,
		"approval_source": current.approval_source,
		"effective_date": current.effective_date,
	}
	try:
		# Exactly the state `restore_matrix_source_wording` leaves behind: the wording is
		# there, and nothing else is -- no status, and none of the three provenance fields
		# the controller requires before it will allow Validated. Stripping the status
		# alone would not reproduce the live site, and a check that reproduces a simpler
		# state than the real one is how the first version of this fix passed while the
		# server still failed. Written straight to the columns, because the controller
		# would refuse to move in this direction.
		frappe.db.set_value(
			"Sparsh Source of Truth Rule", current.name,
			{
				"status": "Draft",
				"approved_by_name": None,
				"approval_source": None,
				"effective_date": None,
			},
			update_modified=False,
		)
		frappe.db.commit()

		result = seed.load_matrix_decisions()

		after = frappe.db.get_value("Sparsh Source of Truth Rule", current.name, "status")
		_assert(
			after == "Validated",
			f"{rule_id} carried his wording and stayed {after} after re-seeding; a decision "
			f"he signed would never reach the engine",
		)
		attached = frappe.db.get_value(
			"Sparsh Source of Truth Rule", current.name,
			["approved_by_name", "approval_source", "effective_date"], as_dict=True,
		)
		_assert(
			attached.approved_by_name and attached.approval_source and attached.effective_date,
			f"{rule_id} reached Validated without an approver, a source and a date attached; "
			f"the engine would be scoring against text whose provenance is gone",
		)
		_assert(
			rule_id in (result.get("promoted_to_validated") or []),
			f"{rule_id} was promoted but not reported under promoted_to_validated; a silent "
			f"status change to a governing rule is the thing that must never be silent",
		)
	finally:
		frappe.db.set_value(
			"Sparsh Source of Truth Rule", current.name, was, update_modified=False,
		)
		frappe.db.commit()


def check_engine_roles_do_not_open_the_desk():
	"""Neither role carries desk access, so holding one never promotes the holder.

	Frappe recomputes `user_type` on every User save: "System User" if any role the user
	holds has `desk_access`, "Website User" otherwise. Both roles were created with it set,
	so granting the programme owner `Sparsh Reviewer` -- purely so he could read three
	portal pages -- promoted him and opened the desk, where seventeen unrelated hospital
	apps live. Every volunteer on the pilot roster would have been promoted the same way.

	This app's whole interface is the portal pages. Nothing in it is worked in the desk,
	so nothing in it should hand out desk access.
	"""
	from sparsh_los.install import ROLES

	opens_the_desk = [
		role for role in ROLES
		if frappe.db.exists("Role", role) and frappe.db.get_value("Role", role, "desk_access")
	]
	_assert(
		not opens_the_desk,
		f"{', '.join(opens_the_desk)} carries desk access, so anyone granted it becomes a "
		f"System User and lands in the desk beside every other app on this bench",
	)


def check_governance_view_shows_the_wording_and_is_gated():
	"""The rules page's read model is reviewer-only and carries his wording, not ours.

	The dashboards answer how learners are doing; none of them answers the question the
	programme owner's review actually turns on -- is the wording the engine scores
	against the wording he signed? Nothing rendered it until this existed, and the
	letter sent to him described a page that did not show it.

	Gated for the same reason `matrix_status` is: an ungated read model is a way round
	the page it feeds.
	"""
	from sparsh_los import seed

	_make_learner(TEST_LEARNER)
	original = frappe.session.user
	try:
		frappe.set_user(TEST_LEARNER)
		# `_refused`, not `_raises`: a permission refusal is not a ValidationError on
		# this version of Frappe, and the wrong helper reports the gate working as the
		# gate failing.
		_refused(
			seed.governance_view,
			"A learner could read the whole rule inventory through the read model",
			expect="reviewer",
		)
	finally:
		frappe.set_user(original)

	view = seed.governance_view()
	_assert(view["rules"], "The governance view holds no rule at all")

	by_id = {r["rule_id"]: r for r in view["rules"]}
	signed = [
		row["rule_id"] for row in seed._rows()
		if row.get("source_status") == "Validated" and row.get("approved_statement")
	]
	_assert(signed, "The matrix source carries no signed decision, so nothing is proved here")

	for rule_id in signed:
		row = by_id.get(rule_id)
		_assert(row, f"{rule_id} is signed in the matrix and absent from the governance view")
		if not row.get("present"):
			continue
		_assert(
			row["in_force"] and row["status"] == "Validated",
			f"{rule_id} is signed but the page would show it as {row['status']}",
		)
		_assert(
			row["approved_statement"],
			f"{rule_id} would render with no approved wording, which is the one thing "
			f"the page exists to show",
		)
		_assert(
			row["approved_by_name"] and row["approval_source"] and row["effective_date"],
			f"{rule_id} would render as in force with no provenance beside it",
		)

	# Counts must describe the rules actually returned, not be computed a second way.
	_assert(
		view["counts"]["total"] == len(view["rules"]),
		f"The page would print {view['counts']['total']} as the total while listing "
		f"{len(view['rules'])} rules",
	)
	_assert(
		view["counts"]["in_force"] == len([r for r in view["rules"] if r.get("in_force")]),
		"The in-force count disagrees with the rules marked in force",
	)
	# The three he named with no matrix row are reported, never given wording of ours.
	_assert(
		view["named_but_not_in_the_matrix"],
		"The rules he named without a matrix row are not reported, so they read as "
		"having been dealt with",
	)


def check_demonstration_data_refuses_without_confirmation():
	"""Synthetic learner records cannot be created by anything that sounded harmless.

	On every page this data is indistinguishable from the real thing, so the only
	protection is that making it has to be deliberate. It must also never be the thing
	that starts the pilot: `SSP-PILOT` is seeded Draft because activating it is the
	programme owner's act, and the demonstration runs on its own pathway.
	"""
	from sparsh_los import demo

	_raises(
		demo.load,
		"The demonstration cohort could be loaded without confirming it",
		expect="confirm",
	)
	_raises(
		demo.remove,
		"The demonstration cohort could be removed without confirming it",
		expect="confirm",
	)
	_assert(
		demo.DEMO_PATHWAY != "SSP-PILOT",
		"The demonstration runs on the pilot pathway, so showing the dashboards would "
		"start the pilot",
	)
	# Every synthetic address must be unroutable: `.invalid` is reserved and can never
	# receive mail, so no message reaches a real person by accident.
	for email, _name in demo.VOLUNTEERS + demo.REVIEWERS:
		_assert(
			email.endswith(".invalid"),
			f"{email} is a deliverable address on a synthetic account",
		)




def check_an_answered_question_reaches_the_learner():
	"""A reviewer's answer is shown to the learner who asked.

	The escalation route is only worth having if the answer comes back. This failed
	in the field: `learner_view` selected questions at `Open` or `Routed to Human`
	and never selected `answer_text`, so the moment a reviewer answered, the question
	left the learner's page and the answer appeared nowhere. A volunteer would ask,
	a reviewer would write a careful reply, and the volunteer would never read it.
	"""
	from sparsh_los import dashboard, escalation

	_make_learner(TEST_LEARNER)
	frappe.db.commit()

	original_user = frappe.session.user
	try:
		frappe.set_user(TEST_LEARNER)
		attempt = frappe.new_doc("Sparsh Attempt")
		attempt.learner = TEST_LEARNER
		attempt.activity = ACTIVITY_1
		attempt.hint_level_used = 0
		attempt.insert()
		question = escalation.raise_question(
			"Does a reduced pledge still count as kept?",
			activity=ACTIVITY_1,
			attempt=attempt.name,
		)
	finally:
		frappe.set_user(original_user)

	answer_text = "Yes. A smaller pledge that is kept beats a larger one abandoned."
	escalation.answer(question, answer_text, "Private answer")
	answered_by = frappe.session.user
	frappe.db.commit()

	try:
		frappe.set_user(TEST_LEARNER)
		view = dashboard.learner_view()

		open_ids = [q["name"] for q in view["open_escalations"]]
		_assert(
			question not in open_ids,
			"An answered question is still listed as awaiting an answer",
		)

		answered = view.get("answered_escalations")
		_assert(
			answered is not None,
			"learner_view returns no answered_escalations: the answer cannot reach the learner",
		)
		mine = [q for q in answered if q["name"] == question]
		_assert(len(mine) == 1, f"The answered question is not in the learner's view: {answered}")
		row = mine[0]
		_assert(
			row.get("answer_text") == answer_text,
			f"The answer text is not carried to the learner: {row.get('answer_text')!r}",
		)
		_assert(
			row.get("question_text"),
			"The answer is shown without the question it answers",
		)
		# The page promises "a named reviewer will read your question and answer it",
		# so the name is part of the promise, not an incidental field.
		_assert(
			row.get("answered_by") == answered_by,
			f"The answer does not name who gave it: {row.get('answered_by')!r}",
		)
		# The disposition is a programme classification, not the learner's business.
		_assert(
			"disposition" not in row,
			"The reviewer's disposition was exposed to the learner",
		)
	finally:
		frappe.set_user(original_user)

	# The read model carrying the answer is necessary and not sufficient: the page has
	# to render it. This asserts the wiring statically -- the harness has no template
	# renderer, and a fresh-interpreter render would not prove what a real request
	# returns anyway. The rendered page is confirmed over real HTTP at deploy time.
	template = frappe.get_app_path("sparsh_los", "www", "practice.html")
	markup = pathlib.Path(template).read_text(encoding="utf-8")
	_assert(
		"answered_escalations" in markup,
		"practice.html never reads answered_escalations, so the answer is fetched and not shown",
	)
	_assert(
		"answer_text" in markup,
		"practice.html does not render answer_text",
	)
	_assert(
		"disposition" not in markup,
		"practice.html would show the reviewer's disposition to the learner",
	)
	frappe.db.commit()



def check_certification_enforces_the_owners_assessment_rule():
	"""Readiness honours the number of cases and unaided passes the owner required.

	`Sparsh Competency` carries three fields the programme owner filled in for the
	safety competency -- `assessment_cases`, `unaided_passes_required` and
	`all_safety_decisions_must_be_correct` -- and until this check existed nothing read
	any of them. `readiness()` returned "the evidence supports sign-off" as soon as
	mastery reached Demonstrated, which one unaided pass achieves. For SSP-SCOPE the
	owner asked for five cases, four of them unaided and every safety decision correct.
	The engine displayed his rule and certified against a different one, which is worse
	than having no rule at all: the person signing was told the evidence met a standard
	it had not met.
	"""
	from sparsh_los import certification

	_reset_competency()
	frappe.db.set_value(
		"Sparsh Competency",
		COMPETENCY,
		{
			"assessment_cases": 2,
			"unaided_passes_required": 2,
			"all_safety_decisions_must_be_correct": 1,
			"threshold_source": "Harness fixture, not a programme decision",
		},
		update_modified=False,
	)
	frappe.db.commit()

	try:
		# One unaided pass reaches Demonstrated, which used to read as ready.
		_new_evidence(ACTIVITY_1, "Pass")
		first = certification.readiness(COMPETENCY, LEARNER)
		_assert(
			first["verdict"] != certification.READY,
			f"One pass certified against a rule asking for two: {first['verdict']} -- {first['reason']}",
		)
		_assert(
			"2" in first["reason"],
			f"The shortfall does not say what was required: {first['reason']}",
		)

		# A second unaided pass on a distinct activity meets it.
		_new_evidence(ACTIVITY_2, "Pass")
		met = certification.readiness(COMPETENCY, LEARNER)
		_assert(
			met["verdict"] == certification.READY,
			f"Two unaided passes across two cases did not satisfy the rule: {met['reason']}",
		)

		# An assisted pass is not an unaided one: it must not close the gap.
		_reset_competency()
		frappe.db.set_value(
			"Sparsh Competency", COMPETENCY,
			{"assessment_cases": 2, "unaided_passes_required": 2,
			 "all_safety_decisions_must_be_correct": 0},
			update_modified=False)
		frappe.db.commit()
		_new_evidence(ACTIVITY_1, "Pass")
		_new_evidence(ACTIVITY_2, "Pass", assistance_level=2)
		assisted = certification.readiness(COMPETENCY, LEARNER)
		_assert(
			assisted["verdict"] != certification.READY,
			"An assisted pass was counted towards an unaided-pass requirement",
		)
	finally:
		_reset_competency()
		frappe.db.set_value(
			"Sparsh Competency", COMPETENCY,
			{"assessment_cases": 0, "unaided_passes_required": 0,
			 "all_safety_decisions_must_be_correct": 0, "threshold_source": None},
			update_modified=False)
		frappe.db.commit()



def check_the_rule_gate_fails_closed_and_reads_automation_status():
	"""No rule linked means no automatic score, and "Do not automate" is obeyed.

	Two holes, both found by an independent audit rather than by this harness.

	The gate returned True for a competency with no rule linked at all, so deleting or
	forgetting a link authorised automatic scoring. §8 asks for the opposite default:
	nothing is automated until a rule says it may be. The old docstring called this an
	accepted risk, which is a fair description of a decision nobody made deliberately.

	The gate also read only `status`. A rule could be Validated -- the owner has ruled
	on the wording -- and still say "Human review required" or "Do not automate" in
	`automation_status`, which is a separate decision about whether a machine may apply
	it. Ruling on wording is not permission to automate.
	"""
	from sparsh_los import runner

	_reset_competency()
	_unlink_rules()

	# Nothing linked: the engine must not score on its own.
	_assert(
		runner._rule_is_validated(COMPETENCY) is False,
		"A competency with no rule linked still authorised automatic scoring",
	)

	rule = _new_rule(1)
	frappe.db.set_value(
		"Sparsh Source of Truth Rule", rule.name,
		{"automation_status": "Safe as fixed logic"}, update_modified=False)
	doc = frappe.get_doc("Sparsh Competency", COMPETENCY)
	doc.append("linked_rules", {"rule": rule.name})
	doc.save(ignore_permissions=True)
	frappe.db.commit()
	_assert(
		runner._rule_is_validated(COMPETENCY) is True,
		"A Validated rule marked safe to automate did not permit scoring",
	)

	for refused in ("Human review required", "Do not automate"):
		frappe.db.set_value(
			"Sparsh Source of Truth Rule", rule.name,
			{"automation_status": refused}, update_modified=False)
		frappe.db.commit()
		_assert(
			runner._rule_is_validated(COMPETENCY) is False,
			f"A Validated rule marked '{refused}' still authorised automatic scoring",
		)

	_reset_competency()
	frappe.db.commit()



def check_demonstration_removal_names_doctypes_that_exist():
	"""Every DocType `demo.remove` claims to clear is one the engine actually writes.

	`remove()` listed "Sparsh Event Log". No such DocType exists -- `events.emit()`
	writes "Sparsh Event" -- and the loop skips any name it cannot find, so the typo
	was silent and every synthetic event row survived a removal reported as complete.
	The programme owner was told the demonstration data goes in one step.

	This asserts against the DocType registry rather than a hardcoded list, so a future
	rename is caught here instead of in a cleanup somebody trusted.
	"""
	from sparsh_los import demo

	for doctype in demo.LEARNER_OWNED_DOCTYPES:
		_assert(
			frappe.db.exists("DocType", doctype),
			f"demo.remove would silently skip {doctype!r}: no such DocType",
		)

	# The engine's own event writer must be covered by that list, or demonstration
	# events outlive the demonstration.
	_assert(
		"Sparsh Event" in demo.LEARNER_OWNED_DOCTYPES,
		"demo.remove does not clear Sparsh Event, which is what events.emit writes",
	)
	_assert(
		"Sparsh Escalation Question" in demo.LEARNER_OWNED_DOCTYPES,
		"demo.remove does not clear the questions the synthetic learners raised",
	)



def check_a_validated_rule_cannot_be_rewritten_in_place():
	"""Once a rule is Validated, its wording and provenance are frozen.

	The controller demanded that a Validated rule carry approved wording, an approver,
	a source and a date -- and then let all four be edited afterwards, silently. A rule
	could be signed, put in force, and reworded in place with nothing but a change-log
	entry to show for it. Versioning existed and was optional, which is the same as not
	existing: the governance page would show the new wording as though the owner had
	approved it.

	Superseding a Validated rule with a new version is the supported route and is
	deliberately left open. Retiring one is also allowed: withdrawing a rule is not
	rewriting what it said.
	"""
	_reset_competency()
	frappe.db.commit()

	rule = _new_rule(1)
	_assert(rule.status == "Validated", "The fixture rule is not Validated")

	frozen = {
		"rule_statement": "A different statement entirely.",
		"approved_statement": "Wording the owner never approved.",
		"approved_by_name": "Somebody Else",
		"approval_source": "A source nobody cited.",
		"effective_date": frappe.utils.add_days(frappe.utils.today(), -30),
		"version": 7,
	}
	for field, value in frozen.items():
		def rewrite(field=field, value=value):
			doc = frappe.get_doc("Sparsh Source of Truth Rule", rule.name)
			setattr(doc, field, value)
			doc.save(ignore_permissions=True)

		_raises(
			rewrite,
			f"A Validated rule's {field} was rewritten in place",
			expect="Validated",
		)

	# Status may still move: retiring or superseding a rule is a decision, not a
	# rewrite of what it said.
	doc = frappe.get_doc("Sparsh Source of Truth Rule", rule.name)
	doc.status = "Context-dependent"
	doc.save(ignore_permissions=True)
	_assert(
		frappe.db.get_value("Sparsh Source of Truth Rule", rule.name, "status")
		== "Context-dependent",
		"A Validated rule's status could not move, which is a decision and not a rewrite",
	)

	# And a Draft rule is freely editable -- the freeze begins at Validated.
	draft = _new_rule(2, status="Draft")
	draft.rule_statement = "Still being drafted."
	draft.save(ignore_permissions=True)
	_assert(
		frappe.db.get_value("Sparsh Source of Truth Rule", draft.name, "rule_statement")
		== "Still being drafted.",
		"A Draft rule could not be edited",
	)

	_reset_competency()
	frappe.db.commit()


CHECKS = (
	("partial_only_history_is_named_accurately", check_partial_only_history_is_named_accurately),
	("queue_shows_work_that_is_actually_waiting", check_queue_shows_work_that_is_actually_waiting),
	("ledger_with_no_price_reports_unknown_not_zero", check_ledger_with_no_price_reports_unknown_not_zero),
	("queue_limit_is_bounded", check_queue_limit_is_bounded),
	("refresh_due_advice_names_the_refresher", check_refresh_due_advice_names_the_refresher),
	("cross_user_endpoints_refuse", check_cross_user_endpoints_refuse),
	("no_programme_name_in_messages", check_no_programme_name_in_messages),
	("unenrolled_learns_nothing_from_the_error", check_unenrolled_learns_nothing_from_the_error),
	("escalation_cannot_be_self_answered_by_update", check_escalation_cannot_be_self_answered_by_update),
	("no_rule_auto_scoring_blocks_the_pilot", check_no_rule_auto_scoring_blocks_the_pilot),
	("resource_lineage_is_checked", check_resource_lineage_is_checked),
	("blank_currency_suppresses_totals", check_blank_currency_suppresses_totals),
	("supervisor_ignores_rejected_evidence", check_supervisor_ignores_rejected_evidence),
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
	("current_resource_material_cannot_change_in_place",
	 check_current_resource_material_cannot_change_in_place),
	("existing_current_resources_are_keyed", check_existing_current_resources_are_keyed),
	("critical_marker_without_rule_blocks_the_pilot", check_critical_marker_without_rule_blocks_the_pilot),
	("nobody_closes_their_own_refresher", check_nobody_closes_their_own_refresher),
	("repeated_failures_reach_the_supervisor_from_attempts",
	 check_repeated_failures_reach_the_supervisor_from_attempts),
	("review_verdict_lands_on_the_evidence", check_review_verdict_lands_on_the_evidence),
	("awaiting_a_person_agrees_with_the_queue", check_awaiting_a_person_agrees_with_the_queue),
	("learner_cannot_read_rules_or_competencies", check_learner_cannot_read_rules_or_competencies),
	("practice_page_records_the_session", check_practice_page_records_the_session),
	("cohort_permission_model", check_cohort_permission_model),
	("cohort_member_indexes_exist", check_cohort_member_indexes_exist),
	("cohort_joined_on_is_server_set", check_cohort_joined_on_is_server_set),
	("cohort_refuses_duplicate_member", check_cohort_refuses_duplicate_member),
	("one_active_cohort_per_learner", check_one_active_cohort_per_learner),
	("active_key_is_enforced_below_validate", check_active_key_is_enforced_below_validate),
	("active_key_survives_ordinary_edit", check_active_key_survives_ordinary_edit),
	("pathway_for_refuses_ambiguity", check_pathway_for_refuses_ambiguity),
	("cohort_members_is_a_reviewer_action", check_cohort_members_is_a_reviewer_action),
	("cohort_readiness_restricts_to_cohort", check_cohort_readiness_restricts_to_cohort),
	("reflection_is_not_queued", check_reflection_is_not_queued),
	("reflection_cannot_become_evidence", check_reflection_cannot_become_evidence),
	("critical_reflection_escalates_without_evidence", check_critical_reflection_escalates_without_evidence),
	("urgency_default_and_freeze", check_urgency_default_and_freeze),
	("open_queue_surfaces_urgency", check_open_queue_surfaces_urgency),
	("escalation_event_logs_urgency", check_escalation_event_logs_urgency),
	("pathway_status_is_respected", check_pathway_status_is_respected),
	("pilot_pathway_is_seeded_draft", check_pilot_pathway_is_seeded_draft),
	("practice_page_prefers_the_assigned_pathway", check_practice_page_prefers_the_assigned_pathway),
	("awaiting_a_person_excludes_reflections", check_awaiting_a_person_excludes_reflections),
	("reflection_does_not_swallow_waiting_work", check_reflection_does_not_swallow_waiting_work),
	("translations_are_imported", check_translations_are_imported),
	("inactive_reconciles", check_inactive_reconciles),
	("last_activity_date_from_event", check_last_activity_date_from_event),
	("refresher_repeated", check_refresher_repeated),
	("refresher_overdue_is_undefined", check_refresher_overdue_is_undefined),
	("escalation_turnaround_median", check_escalation_turnaround_median),
	("state_change_parsed", check_state_change_parsed),
	("attempts_by_competency_sum", check_attempts_by_competency_sum),
	("pathway_completion", check_pathway_completion),
	("rejected_pass_does_not_complete_a_step", check_rejected_pass_does_not_complete_a_step),
	("no_model_share", check_no_model_share),
	("numeric_scores_within_tolerance", check_numeric_scores_within_tolerance),
	("numeric_zero_is_an_answer", check_numeric_zero_is_an_answer),
	("numeric_non_answer_is_refused_not_recorded", check_numeric_non_answer_is_refused_not_recorded),
	("numeric_obeys_the_rule_gate", check_numeric_obeys_the_rule_gate),
	("numeric_critical_marker_still_bites", check_numeric_critical_marker_still_bites),
	("rubric_aggregates_to_partial", check_rubric_aggregates_to_partial),
	("rubric_verdict_issues_a_hint_and_counts_as_assistance",
	 check_rubric_verdict_issues_a_hint_and_counts_as_assistance),
	("rubric_refuses_self_review_and_wrong_mode", check_rubric_refuses_self_review_and_wrong_mode),
	("rubric_is_never_auto_scored", check_rubric_is_never_auto_scored),
	("learner_cannot_read_rubric_or_numeric_key", check_learner_cannot_read_rubric_or_numeric_key),
	("ai_assisted_awaits_implementation", check_ai_assisted_awaits_implementation),
	("certificate_version_increments_across_revocation",
	 check_certificate_version_increments_across_revocation),
	("next_version_places_after_unnumbered_legacy", check_next_version_places_after_unnumbered_legacy),
	("backfill_certificate_versions", check_backfill_certificate_versions),
	("compliance_view_buckets", check_compliance_view_buckets),
	("compliance_view_reuses_readiness", check_compliance_view_reuses_readiness),
	("compliance_renewal_null_reads_as_no_policy", check_compliance_renewal_null_reads_as_no_policy),
	("compliance_view_is_reviewer_gated", check_compliance_view_is_reviewer_gated),
	("compliance_cohort_rate_counts_non_starters", check_compliance_cohort_rate_counts_non_starters),
	("controller_hooks_are_on_the_class", check_controller_hooks_are_on_the_class),
	("validated_means_somebody_validated_it", check_validated_means_somebody_validated_it),
	("owner_decisions_reach_validated", check_owner_decisions_reach_validated),
	("decisions_promote_a_rule_whose_wording_arrived_first", check_decisions_promote_a_rule_whose_wording_arrived_first),
	("engine_roles_do_not_open_the_desk", check_engine_roles_do_not_open_the_desk),
	("governance_view_shows_the_wording_and_is_gated", check_governance_view_shows_the_wording_and_is_gated),
	("demonstration_data_refuses_without_confirmation", check_demonstration_data_refuses_without_confirmation),
	("an_answered_question_reaches_the_learner", check_an_answered_question_reaches_the_learner),
	("certification_enforces_the_owners_assessment_rule", check_certification_enforces_the_owners_assessment_rule),
	("the_rule_gate_fails_closed_and_reads_automation_status", check_the_rule_gate_fails_closed_and_reads_automation_status),
	("demonstration_removal_names_doctypes_that_exist", check_demonstration_removal_names_doctypes_that_exist),
	("a_validated_rule_cannot_be_rewritten_in_place", check_a_validated_rule_cannot_be_rewritten_in_place),
	("matrix_keeps_the_owners_own_words", check_matrix_keeps_the_owners_own_words),
	("mastery_threshold_is_the_owners_not_the_engines",
	 check_mastery_threshold_is_the_owners_not_the_engines),
	("pilot_gate_names_what_is_missing", check_pilot_gate_names_what_is_missing),
	("diagnostic_opens_one_activity_per_competency",
	 check_diagnostic_opens_one_activity_per_competency),
	("diagnostic_assigns_a_refresher_only_where_weak",
	 check_diagnostic_assigns_a_refresher_only_where_weak),
	("refresher_sends_the_learner_to_the_gap", check_refresher_sends_the_learner_to_the_gap),
	("assessment_event_cannot_be_deleted", check_assessment_event_cannot_be_deleted),
	("calibration_reports_the_uncalibrated_as_uncalibrated",
	 check_calibration_reports_the_uncalibrated_as_uncalibrated),
	("calibration_writes_nothing", check_calibration_writes_nothing),
	("pilot_prepare_refuses_an_incomplete_roster",
	 check_pilot_prepare_refuses_an_incomplete_roster),
	("pilot_prepare_leaves_activation_to_a_person",
	 check_pilot_prepare_leaves_activation_to_a_person),
	("cleanup", check_cleanup),
)


def run_one(name):
	"""One check, with the fixtures it assumes. For proving a check fails when its fix is reverted.

	`bench execute sparsh_los.verify.check_x` runs the function bare, against a bench
	that `teardown()` has already emptied of the setup fixtures, so it fails for the
	wrong reason. This gives it the same surroundings `run` does.
	"""
	results.clear()
	frappe.flags.in_mastery_recompute = False
	teardown()
	setup()
	try:
		_check(name, dict(CHECKS)[name])
	finally:
		teardown()
	print(f"RESULT passed={sum(1 for _, ok, _ in results if ok)} failed={sum(1 for _, ok, _ in results if not ok)}")


def run():
	results.clear()
	skipped.clear()
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
	if skipped:
		print("SKIPPED, and why:")
		for name, why in skipped:
			print(f"  {name}: {why}")
	print(f"RESULT passed={passed} failed={failed} skipped={len(skipped)}")

	if failed:
		sys.exit(1)
