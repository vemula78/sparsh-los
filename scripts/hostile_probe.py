"""Hostile-caller probes against the installed app on a development bench.

Every probe states who is calling, what they are attempting, and what the engine did.
A probe that is REFUSED names the exception; a probe that is ALLOWED is marked so it
cannot be skimmed past. Nothing here asserts — it reports, and the reader judges.

Fixtures use the ZZP- prefix (verify.py owns ZZV-) so the two can coexist. Everything
created is deleted in the finally block. No patient data: the subjects are three
throwaway accounts on a clean dev site.
"""

import os
import frappe, traceback

P = "ZZP-"
DOMAIN, COMP, ACT = P + "DOM", P + "COMP", P + "ACT"
A = "zzp-learner-a@example.invalid"     # the attacker
B = "zzp-learner-b@example.invalid"     # the victim, another learner
REV = "zzp-reviewer@example.invalid"    # a legitimate reviewer
NOBODY = "zzp-norole@example.invalid"   # authenticated, enrolled in nothing

rows = []


def probe(who, what, fn):
	"""Run one attempt as `who` and record what came back."""
	original = frappe.session.user
	frappe.db.savepoint("zzp")
	try:
		frappe.set_user(who)
		fn()
	except Exception as exc:
		frappe.db.rollback(save_point="zzp")
		frappe.set_user(original)
		detail = str(exc).strip().split("\n")[0][:90] or "(no message)"
		rows.append(("REFUSED", who.split("@")[0], what, "%s: %s" % (type(exc).__name__, detail)))
		return
	frappe.db.rollback(save_point="zzp")
	frappe.set_user(original)
	rows.append(("**ALLOWED**", who.split("@")[0], what, ""))


def _user(email, roles):
	if not frappe.db.exists("User", email):
		u = frappe.new_doc("User")
		u.email, u.first_name, u.send_welcome_email = email, "Probe", 0
		u.insert(ignore_permissions=True)
	u = frappe.get_doc("User", email)
	have = [r.role for r in u.roles]
	for r in roles:
		if r not in have:
			u.append("roles", {"role": r})
	u.save(ignore_permissions=True)


def cleanup():
	f = frappe.flags
	prev_m, prev_r = f.in_sparsh_maintenance, f.in_mastery_recompute
	# Fixture teardown is maintenance, and says so rather than routing around the
	# guards that make attempts and mastery states undeletable in ordinary use.
	f.in_sparsh_maintenance = f.in_mastery_recompute = True
	try:
		for dt, filt in (
			("Sparsh Escalation Question", {"activity": ACT}),
			("Sparsh Human Review", {"competency": COMP}),
			("Sparsh Certification Record", {"competency": COMP}),
			("Sparsh Evidence", {"competency": COMP}),
			("Sparsh Mastery State", {"competency": COMP}),
			("Sparsh Refresher Assignment", {"competency": COMP}),
			("Sparsh Attempt", {"activity": ACT}),
			("Sparsh Event", {"activity": ACT}),
			("Sparsh Activity", {"activity_id": ACT}),
			("Sparsh Competency", {"competency_id": COMP}),
			("Sparsh Competency Domain", {"domain_id": DOMAIN}),
		):
			for name in frappe.get_all(dt, filters=filt, pluck="name"):
				d = frappe.get_doc(dt, name)
				if d.meta.is_submittable and d.docstatus == 1:
					d.cancel()
				frappe.delete_doc(dt, name, force=True, ignore_permissions=True)
		for email in (A, B, REV, NOBODY):
			if frappe.db.exists("User", email):
				frappe.delete_doc("User", email, force=True, ignore_permissions=True)
	finally:
		f.in_sparsh_maintenance, f.in_mastery_recompute = prev_m, prev_r
	frappe.db.commit()


def setup():
	_user(A, ["Sparsh Learner"]); _user(B, ["Sparsh Learner"])
	_user(REV, ["Sparsh Reviewer"]); _user(NOBODY, [])
	d = frappe.new_doc("Sparsh Competency Domain")
	d.domain_id, d.domain_name = DOMAIN, "Probe Domain"
	d.insert(ignore_permissions=True)
	c = frappe.new_doc("Sparsh Competency")
	c.competency_id, c.competency_name, c.domain = COMP, "Probe Competency", DOMAIN
	c.insert(ignore_permissions=True)
	a = frappe.new_doc("Sparsh Activity")
	a.activity_id, a.title, a.competency = ACT, "Probe Activity", COMP
	a.activity_type, a.instruction, a.version = "Knowledge check", "Probe instruction.", 1
	a.hints = "HINT-CANARY-ONE\nHINT-CANARY-TWO"
	a.expected_response = "ANSWER-KEY-CANARY"
	a.critical_markers = "CRITICAL-CANARY"
	a.insert(ignore_permissions=True)

	# A real attempt by learner B, so A has something of someone else's to reach for.
	frappe.set_user(B)
	from sparsh_los import runner
	runner.start(ACT)
	runner.submit(ACT, "an answer from learner B")
	frappe.set_user("Administrator")
	frappe.db.commit()
	return frappe.get_all("Sparsh Attempt", filters={"learner": B, "activity": ACT}, pluck="name")[0]


def main():
	b_attempt = setup()
	from sparsh_los import runner, review, escalation, dashboard, certification, orchestrator, gateway, seed

	# --- content confidentiality: the answer key and hint ladder -------------------
	# These must go through the permission-applying API. frappe.get_all is
	# get_list(ignore_permissions=True) and frappe.get_doc runs no read check at all,
	# so probing with either measures nothing about what a caller may do — it only
	# proves the row exists. frappe.client.get_list is what a browser actually reaches.
	probe(A, "list Sparsh Activity (client.get_list — the HTTP path)",
		  lambda: _client_list("Sparsh Activity", {"activity_id": ACT}))
	probe(A, "read the Activity document (get_doc + check_permission)",
		  lambda: frappe.get_doc("Sparsh Activity", ACT).check_permission("read"))
	probe(A, "filter Activity on expected_response as a prefix oracle",
		  lambda: _client_list("Sparsh Activity",
							   {"expected_response": ("like", "ANSWER%")}))
	probe(A, "select the hints field through client.get_list",
		  lambda: _client_list("Sparsh Activity", {"activity_id": ACT},
							   fields=["name", "hints", "expected_response"]))

	# --- row scope: another learner's record ---------------------------------------
	for dt in ("Sparsh Attempt", "Sparsh Evidence", "Sparsh Mastery State",
			   "Sparsh Certification Record", "Sparsh Escalation Question",
			   "Sparsh Refresher Assignment"):
		scope_probe(A, dt, B)
	probe(A, "read learner B's attempt by name (get_doc + check_permission)",
		  lambda: frappe.get_doc("Sparsh Attempt", b_attempt).check_permission("read"))

	# --- endpoints that take a learner argument ------------------------------------
	probe(A, "dashboard.learner_view(learner=B)", lambda: dashboard.learner_view(learner=B))
	probe(A, "certification.readiness(learner=B)",
		  lambda: certification.readiness(COMP, learner=B))
	probe(A, "orchestrator.next_experience(learner=B)",
		  lambda: orchestrator.next_experience(COMP, learner=B))

	# --- reviewer-only endpoints called by a learner --------------------------------
	probe(A, "review.pending()", lambda: review.pending())
	probe(A, "review.record_evidence() on B's attempt",
		  lambda: review.record_evidence(b_attempt, "Pass"))
	probe(A, "escalation.open_queue()", lambda: escalation.open_queue())
	probe(A, "escalation.route() a question to themselves",
		  lambda: escalation.route(_a_question(B), A))
	probe(A, "escalation.answer() on B's question",
		  lambda: escalation.answer(_a_question(B), "an answer", "Private answer"))
	probe(A, "dashboard.supervisor_view()", lambda: dashboard.supervisor_view())
	probe(A, "dashboard.competency_heatmap()", lambda: dashboard.competency_heatmap())
	probe(A, "dashboard.programme_summary()", lambda: dashboard.programme_summary())
	probe(A, "certification.cohort_readiness()", lambda: certification.cohort_readiness(COMP))
	probe(A, "gateway.spend()", lambda: gateway.spend())
	probe(A, "seed.programme_readiness()", lambda: seed.programme_readiness())

	# --- forging and self-grading ---------------------------------------------------
	probe(A, "insert an Attempt naming learner B", lambda: _forge_attempt(B))
	probe(A, "insert an Attempt naming themselves with outcome=Pass", lambda: _forge_attempt(A))
	probe(A, "insert Evidence about themselves", lambda: _forge_evidence(A))
	probe(A, "insert a Mastery State directly", lambda: _forge_mastery(A))
	probe(A, "insert a Certification Record for themselves", lambda: _forge_cert(A))
	probe(A, "raise a question about B's attempt",
		  lambda: escalation.raise_question("whose attempt is this?", attempt=b_attempt))
	probe(A, "pass hint_level to runner.submit to claim unaided work",
		  lambda: runner.submit(ACT, "answer", hint_level=0))

	# --- the unenrolled account ------------------------------------------------------
	probe(NOBODY, "runner.start()", lambda: runner.start(ACT))
	probe(NOBODY, "dashboard.learner_view()", lambda: dashboard.learner_view())
	probe(NOBODY, "escalation.raise_question()",
		  lambda: escalation.raise_question("let me in"))
	probe(NOBODY, "certification.readiness()", lambda: certification.readiness(COMP))

	# --- a legitimate reviewer overreaching -------------------------------------------
	probe(REV, "insert a Mastery State directly", lambda: _forge_mastery(B))
	probe(REV, "delete learner B's attempt",
		  lambda: frappe.delete_doc("Sparsh Attempt", b_attempt))
	probe(REV, "write an Event row", lambda: _forge_event())
	probe(REV, "set frappe.flags.in_mastery_recompute then insert Mastery",
		  lambda: _flag_then_forge(B))

	print("%-12s %-18s %-52s %s" % ("RESULT", "CALLER", "ATTEMPT", "REFUSAL"))
	print("-" * 150)
	for r in rows:
		print("%-12s %-18s %-52s %s" % r)
	allowed = [r for r in rows if r[0].startswith("**")]
	print("\n%d probes, %d refused, %d ALLOWED" % (len(rows), len(rows) - len(allowed), len(allowed)))
	for r in allowed:
		print("  ALLOWED: %s -> %s" % (r[1], r[2]))


def _client_list(dt, filters, fields=None):
	"""The list path a logged-in browser reaches: permissions and query conditions apply."""
	import frappe.client
	return frappe.client.get_list(dt, filters=filters, fields=fields or ["name"], limit_page_length=5)


def scope_probe(who, dt, victim):
	"""Row scope reports rows, not exceptions.

	A scoped list does not raise for a forbidden row — it silently returns fewer rows,
	so an exception-shaped probe reads exactly backwards. This one says how many of
	another learner's rows came back, and anything above zero is the finding.
	"""
	original = frappe.session.user
	try:
		frappe.set_user(who)
		got = frappe.get_list(dt, filters={"learner": victim}, limit_page_length=5,
							  ignore_permissions=False)
	except Exception as exc:
		got = None
		detail = "%s: %s" % (type(exc).__name__, str(exc).strip().split("\n")[0][:70])
	finally:
		frappe.set_user(original)
	label = "list %s rows belonging to learner B" % dt
	if got is None:
		rows.append(("REFUSED", who.split("@")[0], label, detail))
	elif got:
		rows.append(("**ALLOWED**", who.split("@")[0], label, "%d row(s) LEAKED" % len(got)))
	else:
		rows.append(("REFUSED", who.split("@")[0], label, "scoped to 0 rows"))


def _must_be_empty(dt, victim):
	"""get_list, not get_all — get_all skips permissions and would pass everything."""
	got = frappe.get_list(dt, filters={"learner": victim}, limit_page_length=5,
						  ignore_permissions=False)
	if got:
		raise AssertionError("returned %d row(s) of another learner" % len(got))


def _a_question(owner):
	original = frappe.session.user
	frappe.set_user("Administrator")
	q = frappe.new_doc("Sparsh Escalation Question")
	q.learner, q.activity, q.question_text, q.status = owner, ACT, "probe question", "Open"
	q.escalation_reason = "Unknown"
	q.insert(ignore_permissions=True)
	frappe.set_user(original)
	return q.name


def _forge_attempt(learner):
	d = frappe.new_doc("Sparsh Attempt")
	d.learner, d.activity, d.outcome, d.hint_level_used = learner, ACT, "Pass", 0
	d.insert()


def _forge_evidence(learner):
	d = frappe.new_doc("Sparsh Evidence")
	d.learner, d.competency, d.activity, d.activity_version = learner, COMP, ACT, 1
	d.outcome, d.assistance_level = "Pass", 0
	d.insert()


def _forge_mastery(learner):
	d = frappe.new_doc("Sparsh Mastery State")
	d.learner, d.competency, d.state = learner, COMP, "Mastered"
	d.insert()


def _forge_cert(learner):
	d = frappe.new_doc("Sparsh Certification Record")
	d.learner, d.competency, d.standing = learner, COMP, "Active"
	d.insert()
	d.submit()


def _forge_event():
	d = frappe.new_doc("Sparsh Event")
	d.event_type, d.learner, d.activity, d.detail = "attempt_submitted", B, ACT, "forged"
	d.insert()


def _flag_then_forge(learner):
	# The flags are the engine's own marker. A request-reachable caller must not be
	# able to set one — but nothing stops a server-side caller, so this probe asks
	# what the guard is actually worth.
	frappe.flags.in_mastery_recompute = True
	try:
		_forge_mastery(learner)
	finally:
		frappe.flags.in_mastery_recompute = False


SITE = os.environ.get("SITE", "sparsh.localhost")
if SITE.endswith("sssihms.org"):
	# This script writes fixtures and logs in as other users. It belongs on a
	# development bench and nowhere near a site carrying real accounts.
	raise SystemExit("refusing to run against %s: development benches only" % SITE)

frappe.init(site=SITE); frappe.connect()
frappe.set_user("Administrator")
try:
	cleanup()
	main()
except Exception:
	traceback.print_exc()
finally:
	frappe.set_user("Administrator")
	cleanup()
	frappe.destroy()
