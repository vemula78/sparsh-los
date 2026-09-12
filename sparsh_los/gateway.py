# Copyright (c) 2026, SSSIHMS and contributors
# For license information, please see license.txt

"""The single place a model call would be accounted for.

**This module makes no model call and opens no socket, and that is not an omission.**
The engine's determinism guarantee holds: nothing here imports a provider SDK, and
`record()` is a ledger entry written by whatever eventually does make the call. The
programme owner asked for provider, model version, prompt version, token counts, cost,
latency, fallback status and a de-identification assertion to be queryable per
interaction *before* any provider is chosen, so that cost per learner, per competency
and per certification can be answered rather than estimated later.

Writing the ledger first is deliberate. A gateway added after the fact is a gateway
somebody routes around: if the only supported way to call a model is one that produces
a row here, the cost and privacy questions stay answerable.

The de-identification flag is the caller's assertion, recorded and never verified —
this module cannot inspect a prompt it never sees. `reject_identifiers` on learner free
text is the control that actually runs; this is the audit trail for what was claimed.
"""

import frappe
from frappe import _

from sparsh_los.permissions import require_reviewer

PURPOSES = (
	"Free-text interpretation",
	"Feedback phrasing",
	"Simulated dialogue",
	"Other",
)


def record(
	provider,
	model_id,
	purpose="Other",
	learner=None,
	competency=None,
	activity=None,
	model_version=None,
	prompt_template=None,
	prompt_version=None,
	input_tokens=None,
	output_tokens=None,
	estimated_cost=None,
	actual_cost=None,
	cost_currency="INR",
	latency_ms=None,
	outcome_status="Success",
	fallback_used=0,
	deidentified=0,
	notes=None,
):
	"""Record one model interaction. Not whitelisted: callers are server-side only."""
	if purpose not in PURPOSES:
		frappe.throw(_("{0} is not a recognised purpose").format(purpose))

	previous = frappe.flags.in_sparsh_gateway
	frappe.flags.in_sparsh_gateway = True
	try:
		doc = frappe.new_doc("Sparsh Model Interaction")
		doc.occurred_at = frappe.utils.now_datetime()
		doc.provider = provider
		doc.model_id = model_id
		doc.model_version = model_version
		doc.purpose = purpose
		doc.learner = learner
		doc.competency = competency
		doc.activity = activity
		doc.prompt_template = prompt_template
		doc.prompt_version = prompt_version
		doc.input_tokens = input_tokens
		doc.output_tokens = output_tokens
		doc.estimated_cost = estimated_cost
		doc.actual_cost = actual_cost
		doc.cost_currency = cost_currency
		doc.latency_ms = latency_ms
		doc.outcome_status = outcome_status
		doc.fallback_used = 1 if fallback_used else 0
		doc.deidentified = 1 if deidentified else 0
		doc.notes = notes
		doc.insert(ignore_permissions=True)
		return doc.name
	finally:
		frappe.flags.in_sparsh_gateway = previous


@frappe.whitelist()
def spend(days=30):
	"""What the model layer cost, and whether anything ran without the assertion.

	Returns zeros while no provider is wired, which is the honest answer rather than
	an empty report: the question "what does this cost per learner" has a value now,
	and it is nil.
	"""
	require_reviewer()

	try:
		days = int(days)
	except (TypeError, ValueError):
		frappe.throw(_("Reporting period must be a whole number of days"))
	if days < 1 or days > 3650:
		frappe.throw(_("Reporting period must be between 1 and 3650 days"))

	since = frappe.utils.add_days(frappe.utils.now_datetime(), -days)
	rows = frappe.get_all(
		"Sparsh Model Interaction",
		filters={"occurred_at": (">=", since)},
		fields=[
			"learner",
			"model_id",
			"input_tokens",
			"output_tokens",
			"estimated_cost",
			"actual_cost",
			"outcome_status",
			"fallback_used",
			"deidentified",
		],
	)

	def cost(row):
		# Actual when the provider reported it, estimate otherwise. Mixing them without
		# saying so would produce a total that looks precise and is not.
		return row.actual_cost if row.actual_cost else (row.estimated_cost or 0)

	learners = {r.learner for r in rows if r.learner}
	total = sum(cost(r) for r in rows)
	return {
		"period_days": days,
		"interactions": len(rows),
		"total_cost": round(total, 4),
		"cost_is_partly_estimated": any(not r.actual_cost for r in rows),
		"learners": len(learners),
		"cost_per_learner": round(total / len(learners), 4) if learners else 0,
		"input_tokens": sum(r.input_tokens or 0 for r in rows),
		"output_tokens": sum(r.output_tokens or 0 for r in rows),
		"errors": len([r for r in rows if r.outcome_status != "Success"]),
		"fallbacks": len([r for r in rows if r.fallback_used]),
		# The number worth escalating: calls made without the caller asserting
		# de-identification. Should be zero, and is a fact rather than a guarantee.
		"without_deidentification_assertion": len([r for r in rows if not r.deidentified]),
		"by_model": sorted({r.model_id for r in rows if r.model_id}),
	}
