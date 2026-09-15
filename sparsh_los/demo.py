# Copyright (c) 2026, SSSIHMS and contributors
# For license information, please see license.txt

"""A synthetic cohort, so the dashboards can be shown before the pilot has run.

Every figure this module produces is invented. None of it describes a real volunteer,
a real attempt, or a real judgement, and none of it may ever be read as programme
evidence. Three things keep that true:

* The accounts are named `Demo Volunteer 01`..`08` and `Demo Reviewer 01`..`02`, at
  addresses under `.invalid` -- a reserved TLD that can never receive mail, so no
  message can reach a real person by accident.
* The work runs on its own pathway, `SSP-DEMO`, and its own cohort. `SSP-PILOT` is
  left in Draft: activating the real pilot is the programme owner's act, and a
  demonstration must not be the thing that starts it.
* `remove()` deletes all of it, including the assessment events that are otherwise
  immutable.

`load()` and `remove()` both refuse unless `confirm=1` is passed. This is data that
looks exactly like the real thing on a page, and it should never be possible to create
it by running something that sounded harmless.
"""

import frappe

from frappe import _

VOLUNTEERS = [(f"demo-volunteer-{i:02d}@sparsh-demo.invalid", f"Demo Volunteer {i:02d}")
			  for i in range(1, 9)]
REVIEWERS = [(f"demo-reviewer-{i:02d}@sparsh-demo.invalid", f"Demo Reviewer {i:02d}")
			 for i in range(1, 3)]

# Everything a synthetic learner owns, in deletion order: children before the parents
# they reference. `Sparsh Event` is the DocType `events.emit()` actually writes -- this
# list said "Sparsh Event Log" for as long as it existed, and because the loop skips a
# DocType it cannot find, the mistake removed nothing and reported success.
#
# Escalation questions are here because a synthetic learner raises them. An answered
# question normally refuses deletion -- it is the record of what a learner was told --
# and `remove()` sets `frappe.flags.in_sparsh_maintenance` precisely so that this
# cleanup, and nothing else, can take them away.
LEARNER_OWNED_DOCTYPES = {
	"Sparsh Assessment Event": "learner",
	"Sparsh Refresher Assignment": "learner",
	"Sparsh Evidence": "learner",
	"Sparsh Mastery State": "learner",
	"Sparsh Escalation Question": "learner",
	"Sparsh Attempt": "learner",
	"Sparsh Event": "learner",
}


DEMO_PATHWAY = "SSP-DEMO"
DEMO_COHORT = "SSPC-DEMO"

# Marks every record this module makes, so `remove()` can find them and a person
# reading the database can tell at a glance what is synthetic.
DEMO_DOMAIN = "@sparsh-demo.invalid"

# What each volunteer does, as (cases attempted, cases left for the reviewer to judge).
# Deliberately uneven: a cohort where everyone is at the same point shows nothing about
# a dashboard whose whole purpose is to find the person who is stuck.
PLAN = [
	{"cases": 5, "unjudged": 0, "fails": 0, "critical": 0},
	{"cases": 5, "unjudged": 1, "fails": 1, "critical": 0},
	{"cases": 4, "unjudged": 0, "fails": 0, "critical": 0},
	{"cases": 4, "unjudged": 2, "fails": 1, "critical": 0},
	{"cases": 3, "unjudged": 1, "fails": 0, "critical": 0},
	{"cases": 3, "unjudged": 0, "fails": 2, "critical": 1},
	{"cases": 2, "unjudged": 2, "fails": 0, "critical": 0},
	{"cases": 1, "unjudged": 0, "fails": 0, "critical": 0},
]


def _guard(confirm, verb):
	if not frappe.utils.cint(confirm):
		frappe.throw(
			_("{0} writes synthetic learner records that look exactly like real ones on "
			  "every page. Pass confirm=1 to say that is what you intend.").format(verb)
		)


def _ensure_user(email, full_name, role):
	if frappe.db.exists("User", email):
		user = frappe.get_doc("User", email)
	else:
		user = frappe.new_doc("User")
		user.email = email
		first, _sep, last = full_name.partition(" ")
		user.first_name = first
		user.last_name = last
		user.enabled = 1
		# Website User, like the programme owner: nothing in this engine is worked in
		# the desk, and a demo account has no business holding desk access.
		user.user_type = "Website User"
		# Frappe emails a welcome message with a password-reset link to every new user.
		# It tried for all ten of these and filled the Error Log with delivery failures.
		# The `.invalid` addresses meant nothing could actually be delivered -- but a
		# demonstration loader must not be in the business of sending mail at all, and
		# relying on the domain to stop it is relying on the second line of defence.
		user.flags.no_welcome_mail = True
		user.send_welcome_email = 0
		user.insert(ignore_permissions=True)
	if role not in [row.role for row in user.roles]:
		user.append("roles", {"role": role})
		user.save(ignore_permissions=True)
	return user.name


def _ensure_pathway(cases):
	"""A demonstration pathway carrying the same nine cases, left Active.

	Separate from `SSP-PILOT` on purpose. The pilot pathway is seeded Draft because
	activating it is what starts the real pilot; a demonstration that flipped it to
	Active would have made that decision on the programme owner's behalf.
	"""
	if frappe.db.exists("Sparsh Pathway", DEMO_PATHWAY):
		doc = frappe.get_doc("Sparsh Pathway", DEMO_PATHWAY)
	else:
		doc = frappe.new_doc("Sparsh Pathway")
		doc.pathway_id = DEMO_PATHWAY
		doc.title = "Demonstration pathway (synthetic)"
		doc.target_role = "Volunteer"
		doc.description = (
			"A copy of the starter case pack carrying synthetic work, so the dashboards "
			"can be shown before the pilot has run. Not the pilot: see SSP-PILOT."
		)
		for index, case in enumerate(cases, start=1):
			doc.append("steps", {
				"step_order": index,
				"activity": case.name,
				"mandatory": 1,
			})
	doc.status = "Active"
	doc.save(ignore_permissions=True)
	return doc.name


def _ensure_cohort(volunteers):
	name = frappe.db.get_value("Sparsh Cohort", {"cohort_id": DEMO_COHORT}, "name")
	doc = frappe.get_doc("Sparsh Cohort", name) if name else frappe.new_doc("Sparsh Cohort")
	if not name:
		doc.cohort_id = DEMO_COHORT
		doc.title = "Demonstration cohort (synthetic)"
	doc.pathway = DEMO_PATHWAY
	doc.status = "Active"
	doc.started_on = doc.started_on or frappe.utils.today()
	held = {row.learner for row in (doc.members or [])}
	for email in volunteers:
		if email not in held:
			doc.append("members", {"learner": email})
	doc.save(ignore_permissions=True)
	return doc.name


@frappe.whitelist()
def load(confirm=0):
	"""Create the synthetic cohort and run it through the case pack.

	Idempotent in the sense that matters: re-running does not create a second cohort or
	a second set of accounts. It does add further attempts, because an attempt is a
	record of something happening and the engine has no notion of "the same attempt
	again" -- call `remove(confirm=1)` first for a clean slate.
	"""
	_guard(confirm, "Loading the demonstration cohort")

	from sparsh_los import review, runner

	cases = frappe.get_all(
		"Sparsh Activity",
		filters={"activity_id": ("like", "SC-%")},
		fields=["name", "activity_id", "competency"],
		order_by="activity_id asc",
	)
	if not cases:
		frappe.throw(_("Load the starter case pack before the demonstration cohort"))

	volunteers = [_ensure_user(email, name, "Sparsh Learner") for email, name in VOLUNTEERS]
	reviewers = [_ensure_user(email, name, "Sparsh Reviewer") for email, name in REVIEWERS]
	_ensure_pathway(cases)
	_ensure_cohort(volunteers)
	frappe.db.commit()

	original = frappe.session.user
	attempts_made, verdicts, left_for_review = 0, 0, 0
	try:
		for index, learner in enumerate(volunteers):
			plan = PLAN[index % len(PLAN)]
			frappe.set_user(learner)
			made = []
			for case in cases[: plan["cases"]]:
				runner.start(case.name)
				# The response text is synthetic and says so. It is never scored by the
				# engine -- every starter case is human-reviewed, because not one of the
				# rules behind them was validated when they were written.
				# No account name, no identifier of any kind in the text. The engine
				# scans response bodies for anything resembling a patient identifier and
				# refuses the attempt, and an email address tripped it -- correctly. The
				# response says what it is and nothing more.
				runner.submit(
					case.name,
					f"Synthetic demonstration response for case {case.activity_id}. "
					f"Written by the demonstration loader, not by a person.",
				)
				made.append(case)
				attempts_made += 1
			frappe.db.commit()

			# A reviewer judges all but the ones deliberately left in the queue, so the
			# queue page has something in it and the dashboards have something to show.
			frappe.set_user(reviewers[index % len(reviewers)])
			to_judge = made[: len(made) - plan["unjudged"]] if plan["unjudged"] else made
			left_for_review += len(made) - len(to_judge)
			fails_left, critical_left = plan["fails"], plan["critical"]
			for case in to_judge:
				attempt = frappe.db.get_value(
					"Sparsh Attempt",
					{"learner": learner, "activity": case.name},
					"name",
					order_by="creation desc",
				)
				if not attempt:
					continue
				if fails_left:
					outcome, critical = "Fail", 1 if critical_left else 0
					fails_left -= 1
					critical_left = max(0, critical_left - critical)
				else:
					outcome, critical = "Pass", 0
				review.record_evidence(
					attempt,
					outcome=outcome,
					assistance_level=0,
					critical_error=critical,
					comments="Synthetic demonstration verdict. Not a judgement of any person.",
				)
				verdicts += 1
			frappe.db.commit()
	finally:
		frappe.set_user(original)

	frappe.db.commit()
	return {
		"cohort": DEMO_COHORT,
		"pathway": DEMO_PATHWAY,
		"pilot_pathway_untouched": frappe.db.get_value("Sparsh Pathway", "SSP-PILOT", "status"),
		"volunteers": len(volunteers),
		"reviewers": len(reviewers),
		"attempts": attempts_made,
		"verdicts_recorded": verdicts,
		"left_awaiting_review": left_for_review,
		"everything_here_is_synthetic": True,
	}


@frappe.whitelist()
def remove(confirm=0):
	"""Delete every record this module made, assessment events included.

	`Sparsh Assessment Event` refuses deletion because a checkpoint record is evidence.
	That refusal is right for real evidence and wrong for invented evidence, so the
	maintenance flag the DocType already honours is set here -- deliberately, in the
	one place whose whole purpose is to take synthetic data back out.
	"""
	_guard(confirm, "Removing the demonstration cohort")

	emails = [email for email, _name in VOLUNTEERS + REVIEWERS]
	deleted = {}

	frappe.flags.in_sparsh_maintenance = True
	try:
		# Children before parents: evidence and events reference attempts.
		for doctype, field in LEARNER_OWNED_DOCTYPES.items():
			if not frappe.db.exists("DocType", doctype):
				# Kept as a guard against a future rename, but it is no longer a place
				# a typo can hide: `demonstration_removal_names_doctypes_that_exist`
				# asserts every name here exists. It used to say "Sparsh Event Log",
				# which does not, so every synthetic event survived a removal reported
				# as complete.
				frappe.log_error(
					title="Sparsh demo cleanup names a missing DocType",
					message=f"{doctype} is not installed; demonstration rows of that "
							f"kind were not removed.",
				)
				continue
			names = frappe.get_all(doctype, filters={field: ("in", emails)}, pluck="name")
			for name in names:
				frappe.delete_doc(doctype, name, force=True, ignore_permissions=True,
								  delete_permanently=True)
			deleted[doctype] = len(names)

		for cohort in frappe.get_all("Sparsh Cohort", filters={"cohort_id": DEMO_COHORT},
									 pluck="name"):
			frappe.delete_doc("Sparsh Cohort", cohort, force=True, ignore_permissions=True)
		deleted["Sparsh Cohort"] = 1 if frappe.db.exists("Sparsh Cohort", {"cohort_id": DEMO_COHORT}) is None else 0

		if frappe.db.exists("Sparsh Pathway", DEMO_PATHWAY):
			frappe.delete_doc("Sparsh Pathway", DEMO_PATHWAY, force=True, ignore_permissions=True)
			deleted["Sparsh Pathway"] = 1

		for email in emails:
			if frappe.db.exists("User", email):
				frappe.delete_doc("User", email, force=True, ignore_permissions=True)
		deleted["User"] = len(emails)
	finally:
		frappe.flags.in_sparsh_maintenance = False

	frappe.db.commit()
	return {"deleted": deleted, "pilot_pathway": frappe.db.get_value(
		"Sparsh Pathway", "SSP-PILOT", "status")}
