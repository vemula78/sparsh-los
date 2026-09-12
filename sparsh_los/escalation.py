# Copyright (c) 2026, SSSIHMS and contributors
# For license information, please see license.txt

"""Human escalation: a core path, not an exception.

When a learner meets something the engine should not resolve — an ambiguous case, a
question outside the approved rules, anything touching medication or safety — the
question goes to a person, with enough context for that person to answer it in one
sitting.

The reviewer's answer is then classified by what it is worth beyond this learner:
private to them, a reusable answer, a change to the source of truth, or a change to
the curriculum. That classification is what turns a support queue into a feedback
loop on the programme itself.
"""

import json

import frappe
from frappe import _

from sparsh_los.permissions import is_restricted, is_reviewer, require_enrolment, require_reviewer, throttle

DISPOSITIONS = (
	"Private answer",
	"Reusable FAQ",
	"Source-of-truth update",
	"Curriculum change",
)

# Dispositions that say the programme, not the learner, needs to change.
PROGRAMME_DISPOSITIONS = ("Source-of-truth update", "Curriculum change")


def _emit_opened(question):
	from sparsh_los import events

	events.emit(
		events.ESCALATION_OPENED,
		learner=question.learner,
		activity=question.activity,
		detail=f"reason={question.escalation_reason}",
		reference_doctype=question.doctype,
		reference_name=question.name,
	)


def _context(activity, attempt):
	"""A compact snapshot of what the learner was doing. No caregiver data."""
	snapshot = {}

	if activity:
		row = frappe.db.get_value(
			"Sparsh Activity", activity, ["title", "competency", "activity_type", "version"], as_dict=True
		)
		if row:
			snapshot["activity"] = dict(row, name=activity)

	if attempt:
		row = frappe.db.get_value(
			"Sparsh Attempt",
			attempt,
			["outcome", "hint_level_used", "retry_index", "critical_error", "rule", "rule_version"],
			as_dict=True,
		)
		if row:
			snapshot["attempt"] = dict(row, name=attempt)

	return json.dumps(snapshot, indent=1, default=str)


@frappe.whitelist()
def raise_question(question_text, activity=None, attempt=None, reason="Unknown"):
	"""A learner asks for expert guidance. Context is packaged here, not by the caller."""
	# This endpoint writes, and its response embeds Activity fields that a learner may
	# not read directly — so it needs the same enrolment gate as every other own-record
	# endpoint, not just the own-attempt check below.
	require_enrolment()

	if not (question_text or "").strip():
		frappe.throw(_("A question cannot be empty"))

	throttle("Sparsh Escalation Question", limit=20)

	if attempt:
		owner = frappe.db.get_value("Sparsh Attempt", attempt, "learner")
		if owner and owner != frappe.session.user and is_restricted():
			frappe.throw(
				_("You can only raise a question about your own attempt"), frappe.PermissionError
			)

	question = frappe.new_doc("Sparsh Escalation Question")
	question.learner = frappe.session.user
	question.activity = activity
	question.attempt = attempt
	question.escalation_reason = reason
	question.question_text = question_text
	question.status = "Open"
	question.context_snapshot = _context(activity, attempt)
	question.insert(ignore_permissions=True)
	_emit_opened(question)

	return question.name


@frappe.whitelist()
def route(question, reviewer):
	"""Put the question in a named reviewer's queue."""
	require_reviewer()

	if not is_reviewer(reviewer):
		frappe.throw(_("{0} is not a reviewer").format(reviewer))

	doc = frappe.get_doc("Sparsh Escalation Question", question)
	doc.routed_to = reviewer
	doc.status = "Routed to Human"
	doc.save()
	return doc.name


@frappe.whitelist()
def answer(question, answer_text, disposition):
	"""Answer a question and classify what the answer is worth.

	Answering is a reviewer action: the write permission on the DocType is the gate,
	so a learner calling this is refused by the ordinary permission check.
	"""
	if disposition not in DISPOSITIONS:
		frappe.throw(_("{0} is not a recognised disposition").format(disposition))

	if not (answer_text or "").strip():
		frappe.throw(_("An answer cannot be empty"))

	require_reviewer()

	doc = frappe.get_doc("Sparsh Escalation Question", question)
	if doc.status == "Answered":
		# Overwriting an answer erases what the learner was actually told.
		frappe.throw(_("This question has already been answered"))

	if doc.learner == frappe.session.user:
		frappe.throw(_("You cannot answer your own question"), frappe.PermissionError)

	doc.answer_text = answer_text
	doc.disposition = disposition
	doc.answered_by = frappe.session.user
	doc.answered_at = frappe.utils.now_datetime()
	doc.status = "Answered"
	doc.save()

	return {
		"question": doc.name,
		"disposition": disposition,
		# A programme-level disposition is the signal that content or rules must change.
		# Acting on it — marking activities for review, setting learners Refresh Due —
		# is deliberately not automated yet: it is a programme decision, not a code one.
		"programme_change_required": disposition in PROGRAMME_DISPOSITIONS,
	}


@frappe.whitelist()
def open_queue(reviewer=None):
	"""The reviewer's queue: oldest first, because a waiting learner is blocked."""
	require_reviewer()

	filters = {"status": ("in", ("Open", "Routed to Human"))}
	if reviewer:
		filters["routed_to"] = reviewer

	return frappe.get_all(
		"Sparsh Escalation Question",
		filters=filters,
		fields=["name", "learner", "activity", "escalation_reason", "question_text", "raised_at", "status"],
		order_by="creation asc",
	)


def raise_for_critical_error(attempt, activity, learner):
	"""Put a critical result in front of a person.

	The runner used to tell the learner a reviewer had been notified when nothing was
	notified and the event only surfaced if a supervisor happened to open a dashboard.
	"""
	question = frappe.new_doc("Sparsh Escalation Question")
	question.learner = learner
	question.activity = activity
	question.attempt = attempt
	question.escalation_reason = "Safety critical"
	question.question_text = (
		"Automatic escalation: this response matched a declared critical error for the activity. "
		"A reviewer should confirm whether the response was genuinely unsafe and, if it was not, "
		"clear the evidence."
	)
	question.status = "Open"
	question.context_snapshot = _context(activity, attempt)
	question.insert(ignore_permissions=True)
	_emit_opened(question)
	return question.name
