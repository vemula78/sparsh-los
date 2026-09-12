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

	context.next_up = None
	for competency in candidates:
		suggestion = orchestrator.next_experience(competency)
		if suggestion.get("activity"):
			context.next_up = dict(suggestion, competency=competency)
			break

	if context.next_up:
		activity = frappe.db.get_value(
			"Sparsh Activity",
			context.next_up["activity"],
			["title", "instruction"],
			as_dict=True,
		)
		context.next_up.update(activity or {})

	return context
