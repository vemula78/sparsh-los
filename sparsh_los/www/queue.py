# Copyright (c) 2026, SSSIHMS and contributors
# For license information, please see license.txt

"""The reviewer's working surface.

Three queues, in the order that matters: safety first, then people waiting on a
judgement, then people waiting on an answer. A reviewer should be able to see what
needs them without constructing a query.
"""

import frappe

from sparsh_los.permissions import require_reviewer

no_cache = 1


def get_context(context):
	require_reviewer()

	from sparsh_los import dashboard, escalation, review

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

	context.pending_reviews = review.pending(limit=25)
	context.escalations = escalation.open_queue()
	context.cohort = dashboard.supervisor_view()

	return context
