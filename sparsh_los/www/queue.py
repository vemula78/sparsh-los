# Copyright (c) 2026, SSSIHMS and contributors
# For license information, please see license.txt

"""The reviewer's working surface.

Three queues, in the order that matters: safety first, then people waiting on a
judgement, then people waiting on an answer. A reviewer should be able to see what
needs them without constructing a query.

This page is reviewer-gated and shows permlevel-1 content (rubric criteria). Nothing
here may be echoed into a URL or reach a learner: the verdict is posted by
`frappe.call` and the page is rendered only after `require_reviewer()` has passed.
"""

import frappe

from sparsh_los.permissions import require_reviewer

no_cache = 1


def get_context(context):
	# First, before any read: a learner reaching this page is refused, not shown an
	# empty page.
	require_reviewer()

	from sparsh_los import dashboard, escalation, review
	from sparsh_los.runner import RUBRIC_MODE, _lines

	from sparsh_los.branding import masthead

	context.branding = masthead()
	context.no_cache = 1
	context.reviewer = frappe.session.user

	# Safety first: standing critical errors nobody has cleared.
	context.critical = frappe.get_all(
		"Sparsh Evidence",
		filters={"critical_error": 1, "critical_error_cleared": 0, "docstatus": 1},
		fields=["name", "learner", "competency", "activity", "recorded_at"],
		order_by="creation desc",
		limit=25,
	)

	pending = review.pending(limit=25)

	# The row carries what the engine returns. The page adds what a reviewer needs to
	# judge without opening another record: the instruction the learner was given,
	# the criteria as numbered lines (line 1 is criterion 1, the shape
	# `score_rubric` expects back), and whether this is the reviewer's own work --
	# the engine refuses that, and the page should say so before a verdict is typed
	# rather than after.
	instructions = {}
	for row in pending:
		if row.activity not in instructions:
			instructions[row.activity] = frappe.db.get_value("Sparsh Activity", row.activity, "instruction")
		row.instruction = instructions[row.activity]
		row.is_rubric = (row.evaluation_mode or "") == RUBRIC_MODE
		row.criteria = _lines(row.rubric_criteria) if row.is_rubric else []
		# The outcome for every possible number of ticks, computed by the engine's own
		# `aggregate_rubric`. The page used to reimplement that rule in JavaScript to
		# preview it live -- correct today, and a lie the moment the rule changes. Its
		# docstring says a threshold would be read there, so it is expected to change,
		# and a reviewer would then be told their ticks produce Partial while the engine
		# recorded something else. One source of truth, looked up rather than repeated.
		row.outcome_for_count = (
			[review.aggregate_rubric(len(row.criteria), met) for met in range(len(row.criteria) + 1)]
			if row.is_rubric
			else []
		)
		row.is_own = row.learner == frappe.session.user
	context.pending_reviews = pending

	escalations = escalation.open_queue()
	for row in escalations:
		row.is_own = row.learner == frappe.session.user
		row.activity_title = (
			frappe.db.get_value("Sparsh Activity", row.activity, "title") if row.activity else None
		)
	context.escalations = escalations

	# The engine's vocabulary, not the template's: an outcome or disposition typed into
	# the HTML and not in these tuples is refused by the server, and the page would
	# look broken for a reason nobody could see.
	context.outcomes = review.REVIEWABLE_OUTCOMES
	context.dispositions = escalation.DISPOSITIONS
	context.urgencies = escalation.URGENCIES

	context.cohort = dashboard.supervisor_view()

	return context
