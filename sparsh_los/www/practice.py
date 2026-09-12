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

	context.next_up = None
	for row in context.view["competencies"]:
		suggestion = orchestrator.next_experience(row["competency"])
		if suggestion.get("activity"):
			context.next_up = dict(suggestion, competency=row["competency"])
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
