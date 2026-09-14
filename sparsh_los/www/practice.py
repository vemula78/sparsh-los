# Copyright (c) 2026, SSSIHMS and contributors
# For license information, please see license.txt

"""The learner's practice page.

Deliberately one task at a time. The learner should always know what they are trying
to do, get only as much help as they need, and leave able to see what they have
demonstrated — not browse a catalogue.
"""

import frappe

no_cache = 1


def get_context(context):
	if frappe.session.user == "Guest":
		frappe.throw("Please sign in", frappe.PermissionError)

	from sparsh_los import dashboard

	context.no_cache = 1
	context.learner = frappe.session.user
	context.view = dashboard.learner_view()

	# Opening this page is the learner's session. Emitted after learner_view, whose
	# require_enrolment has already refused anyone not on the programme, so an
	# unenrolled account does not register as a session.
	from sparsh_los import events

	events.emit(events.SESSION_STARTED, learner=frappe.session.user)

	# The next thing to do, for the first competency that has one.
	from sparsh_los import orchestrator

	# Competencies the learner has touched, then every competency that has activities.
	# Looking only at existing Mastery State rows meant a learner with no evidence saw
	# "Nothing is waiting" and had no way to begin at all.
	seen = [row["competency"] for row in context.view["competencies"]]
	available = frappe.get_all(
		"Sparsh Activity", fields=["competency"], distinct=True, pluck="competency"
	)
	candidates = seen + [c for c in sorted(set(available)) if c and c not in seen]

	# An assigned pathway outranks "whatever this learner could usefully do next".
	# Acceptance criterion 1 asks that a learner enter an *assigned* competency pathway,
	# and until cohorts existed nothing connected a learner to one: this page asked the
	# orchestrator for a suggestion per competency, which answers a different question.
	#
	# The fallback is kept deliberately. A learner in no cohort, or in a cohort whose
	# pathway is still Draft, must still be able to practise -- the alternative is a
	# learner who can see the page and do nothing on it, which is how this page behaved
	# before it looked at Mastery States at all.
	context.next_up = None
	context.pathway = None
	# Set explicitly rather than only in the failure branch: an undefined name is
	# falsy in the template today, which makes the notice depend on Jinja's undefined
	# behaviour rather than on the page saying what it means.
	context.pathway_problem = None

	from sparsh_los import cohort

	# `pathway_for` refuses to choose when a learner somehow sits in two Active cohorts
	# -- rightly, because picking one silently would put them through the wrong
	# programme. But that is an administrator's misconfiguration, and on a page render
	# an uncaught throw is a stack trace where the learner's work should be. The page
	# says what is wrong and still offers the ordinary practice route.
	try:
		assigned = cohort.pathway_for(frappe.session.user)
	except frappe.ValidationError as exc:
		context.pathway_problem = str(exc)
		assigned = None

	if assigned:
		step = orchestrator.next_in_pathway(assigned)
		if step.get("activity"):
			context.pathway = assigned
			context.next_up = step

	if not context.next_up:
		for competency in candidates:
			suggestion = orchestrator.next_experience(competency)
			if suggestion.get("activity"):
				context.next_up = dict(suggestion, competency=competency)
				break

	if context.next_up:
		# Through the runner, not a direct read: `runner.start` is what emits
		# activity_started, and reading title/instruction here meant the event fired
		# only from the harness and the frequency-of-use metric read zero in real use.
		# Its only write is that event, which never raises, so a page render is safe.
		from sparsh_los import runner

		opened = runner.start(context.next_up["activity"])
		# `start` reports the learner's real position now that a reviewer's Rubric
		# verdict can issue a hint. That hint arrives after the session which produced
		# the attempt has closed, so this page is its only route to the learner: drop it
		# here and a Partial verdict's help is written and never delivered.
		context.next_up.update(
			{
				"title": opened["title"],
				"instruction": opened["instruction"],
				"hint": opened["hint"],
				"hint_level": opened["hint_level"],
				"retry_index": opened["retry_index"],
			}
		)

	return context
