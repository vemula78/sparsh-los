# Copyright (c) 2026, SSSIHMS and contributors
# For license information, please see license.txt

"""The supervisor and programme dashboard: section 18's four views on one page.

Every figure on the page is read from the engine's own read models and rendered as
given. Where the engine declines to give a figure -- refresher overdue has no due-date
policy, a median has no timed sample -- the page shows the engine's explanation and
never a zero. Nothing is computed here; a number that is not in the response does not
appear on the page.
"""

import frappe

from sparsh_los.mastery import DEMONSTRATED, MASTERED, PRACTISING, REFRESH_DUE
from sparsh_los.permissions import require_reviewer

no_cache = 1

# Mastery state -> the shared semantic pill. Exploring and Not Started fall to the
# neutral pill: neither is a warning, and giving them one would make the heatmap shout.
PILL_FOR_STATE = {
	MASTERED: "sp-pill--mastered",
	DEMONSTRATED: "sp-pill--demonstrated",
	PRACTISING: "sp-pill--practising",
	REFRESH_DUE: "sp-pill--due",
}

PILL_FOR_COMPLIANCE = {
	"Certified": "sp-pill--mastered",
	"Renewal due": "sp-pill--due",
	"Pending sign-off": "sp-pill--demonstrated",
	"Not yet ready": "sp-pill--blocked",
}


def _date(value):
	"""DD-MMM-YYYY for display; an absent value stays absent."""
	if not value:
		return None
	return frappe.utils.formatdate(frappe.utils.getdate(value), "dd-MMM-yyyy")


def _datetime(value):
	if not value:
		return None
	return frappe.utils.format_datetime(value, "dd-MMM-yyyy HH:mm")


def _percent(fraction):
	"""Display form of a fraction the engine already computed. Never derives one."""
	if fraction is None:
		return None
	return f"{fraction * 100:.1f}%"


def get_context(context):
	require_reviewer()

	from sparsh_los import certification, dashboard, pilot

	from sparsh_los.branding import masthead

	context.branding = masthead()
	context.no_cache = 1
	context.viewer = frappe.session.user

	context.pilot = pilot.status()
	context.cohort = dashboard.supervisor_view()
	context.heatmap = dashboard.competency_heatmap()
	context.summary = dashboard.programme_summary()
	context.compliance = certification.compliance_view()

	context.pill_for_state = PILL_FOR_STATE
	context.pill_for_compliance = PILL_FOR_COMPLIANCE
	context.fmt_date = _date
	context.fmt_datetime = _datetime
	context.fmt_percent = _percent
	context.generated_at = _datetime(frappe.utils.now_datetime())

	return context
