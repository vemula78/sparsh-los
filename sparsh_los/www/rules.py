# Copyright (c) 2026, SSSIHMS and contributors
# For license information, please see license.txt

"""What the engine will judge people against, and who said so.

The dashboards answer "how are the learners doing". This page answers the question
that has to be settled before those figures mean anything: is the wording the engine
scores against the wording the programme owner actually approved?

So every rule is shown with his approved statement beside the statement originally
proposed, and with the provenance that lets it be Validated at all. A rule still
awaiting his decision is shown as such and is not quietly omitted -- the engine
refuses to score against it, and the page has to say so plainly rather than leave a
reviewer to assume the rule is in force.

Nothing is computed here. The read model is `seed.governance_view()`; this page
formats dates and renders what it is given.
"""

import frappe

from sparsh_los.permissions import require_reviewer

no_cache = 1

# A rule's status, as a semantic pill. `Draft` is not a warning -- it is the correct
# resting state for a rule nobody has ruled on -- but it must not read as "in force".
PILL_FOR_STATUS = {
	"Validated": "sp-pill--mastered",
	"Draft": "sp-pill--due",
	"Retired": "sp-pill--blocked",
}


def _date(value):
	"""DD-MMM-YYYY for display; an absent date stays absent rather than becoming today."""
	if not value:
		return None
	return frappe.utils.formatdate(frappe.utils.getdate(value), "dd-MMM-yyyy")


def get_context(context):
	# Before any read: the rule inventory is programme internals.
	require_reviewer()

	from sparsh_los import seed

	from sparsh_los.branding import masthead

	context.branding = masthead()
	context.no_cache = 1
	context.viewer = frappe.session.user

	context.governance = seed.governance_view()
	context.pill_for_status = PILL_FOR_STATUS
	context.fmt_date = _date
	context.generated_at = frappe.utils.format_datetime(
		frappe.utils.now_datetime(), "dd-MMM-yyyy HH:mm"
	)

	return context
