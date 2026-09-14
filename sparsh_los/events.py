# Copyright (c) 2026, SSSIHMS and contributors
# For license information, please see license.txt

"""A lightweight usage log, kept from the start so the analytics layer has a past.

The programme owner asked for this explicitly and asked for it to stay small: an
analytics warehouse is not wanted in the MVP, but the events it would consume are
expensive to invent retrospectively, because nobody can go back and observe what
learners did last month.

Two rules hold this to something safe to keep:

- **Counts and states, never content.** A learner's typed response, a caregiver's
  details and an activity's answer key never reach an event. `detail` takes short
  factual strings the engine composes, not text a user supplied.
- **Emitting never breaks the thing it observes.** A failure to log is logged and
  swallowed. A learner losing their attempt because the telemetry table was locked
  would be a worse outcome than a gap in a chart.
"""

import frappe

# The events the programme owner named, as constants so a typo cannot quietly create
# a second event type that no report ever finds.
SESSION_STARTED = "session_started"
ACTIVITY_STARTED = "activity_started"
ACTIVITY_COMPLETED = "activity_completed"
HINT_SHOWN = "hint_shown"
RETRY_MADE = "retry_made"
CRITICAL_ERROR_RECORDED = "critical_error_recorded"
ESCALATION_OPENED = "escalation_opened"
HUMAN_REVIEW_COMPLETED = "human_review_completed"
MASTERY_STATE_CHANGED = "mastery_state_changed"
REFRESHER_ASSIGNED = "refresher_assigned"
REFRESHER_COMPLETED = "refresher_completed"
CERTIFICATION_STATE_CHANGED = "certification_state_changed"

# The events a learner causes by doing something. The rest of the list is the engine or
# a reviewer acting *about* a learner -- a review completed, a mastery state recomputed,
# a refresher assigned, a certificate suspended. "When was this learner last active?"
# must not be answered by somebody else's activity: a reviewer working through a backlog
# would otherwise make every dormant learner in it look like they had just come back,
# and the drop-off figure the programme is meant to act on would quietly empty itself.
LEARNER_INITIATED = (
	SESSION_STARTED,
	ACTIVITY_STARTED,
	ACTIVITY_COMPLETED,
	HINT_SHOWN,
	RETRY_MADE,
	CRITICAL_ERROR_RECORDED,
	ESCALATION_OPENED,
)


def emit(
	event_type,
	learner=None,
	competency=None,
	activity=None,
	detail=None,
	reference_doctype=None,
	reference_name=None,
):
	"""Record one event. Returns the name, or None if it could not be written."""
	previous = frappe.flags.in_sparsh_event
	frappe.flags.in_sparsh_event = True
	try:
		doc = frappe.new_doc("Sparsh Event")
		doc.event_type = event_type
		doc.occurred_at = frappe.utils.now_datetime()
		doc.learner = learner
		doc.competency = competency
		doc.activity = activity
		doc.reference_doctype = reference_doctype
		doc.reference_name = reference_name
		doc.detail = detail
		doc.insert(ignore_permissions=True)
		return doc.name
	except Exception:  # noqa: BLE001
		# Deliberately broad: nothing this module does is worth failing a learner's
		# attempt for. frappe.log_error keeps the traceback without a message that
		# could carry response text.
		frappe.log_error(title="sparsh_los event not recorded")
		return None
	finally:
		frappe.flags.in_sparsh_event = previous


def count(event_type, since=None):
	"""How many of one event type. The smallest thing a report needs."""
	filters = {"event_type": event_type}
	if since:
		filters["occurred_at"] = (">=", since)
	return frappe.db.count("Sparsh Event", filters)
