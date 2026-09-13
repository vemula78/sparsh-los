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
this module cannot inspect a prompt it never sees.

It *can* inspect what it is asked to store, and does: `reject_identifiers` runs over
`notes`, `prompt_template` and the version strings. Those are the fields a hurried
caller with no template registry will put a rendered prompt into, and they land on a
row two roles can read and export. An earlier version of this docstring said
`reject_identifiers` "is the control that actually runs" while this module did not call
it at all.

What `spend()` answers today is cost per learner, over rows that name a learner. Cost
per competency and per certification are **not** implemented: the columns are recorded
so the history exists when those reports are written, which is the whole reason for
building the ledger before the caller.

And `record()` is not yet the only way to call a model, because nothing calls a model.
There is no interception point in this app; making this the sole route is a decision
for whoever wires the first provider, not something already enforced here.
"""

import frappe
from frappe import _

from sparsh_los.permissions import reject_identifiers, require_reviewer

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
	"""Record one model interaction. Not whitelisted: callers are server-side only.

	Unlike `events.emit`, a failure here is **not** swallowed. That is deliberate and it
	is the opposite trade: an event is an observation, and losing one is better than
	losing a learner's attempt, but an unrecorded model call is money spent with no
	record of spending it -- which is the single thing this ledger exists to prevent. A
	caller that would rather lose the record than the work should catch it and say so.
	"""
	if purpose not in PURPOSES:
		frappe.throw(_("{0} is not a recognised purpose").format(purpose))

	# The free-text surface, checked before it is stored. Cheap, and it is the app's
	# established control for exactly this accident.
	reject_identifiers(notes, prompt_template, prompt_version, model_version)

	previous = frappe.flags.in_sparsh_gateway
	frappe.flags.in_sparsh_gateway = True
	try:
		doc = frappe.new_doc("Sparsh Model Interaction")
		doc.occurred_at = frappe.utils.now_datetime()
		# Normalised: `by_model` and the cost index treat these as buckets, so
		# claude-opus-5 and Claude-Opus-5 would be two models and two totals.
		doc.provider = (provider or "").strip().lower()
		doc.model_id = (model_id or "").strip().lower()
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
		doc.actual_cost = actual_cost or 0
		doc.actual_cost_recorded = 1 if actual_cost is not None else 0
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


def reconcile(interaction, actual_cost, notes=None):
	"""Write the provider's billed cost onto an existing row.

	`spend()` falls back to the estimate when no actual cost is recorded, which presumes
	an actual that arrives later -- providers reconcile in batch, and a streaming call
	reports usage after the fact. The ledger refused every update, so the only way to
	correct a row was to insert a second one, which double-counts, or to delete and
	reinsert, which is a worse audit trail than an amendment.

	Deliberately narrow: this is the only field an amendment may touch. Everything else
	about an interaction is what happened, and stays as recorded.
	"""
	reject_identifiers(notes)

	previous = frappe.flags.in_sparsh_gateway
	frappe.flags.in_sparsh_gateway = True
	try:
		doc = frappe.get_doc("Sparsh Model Interaction", interaction)
		doc.actual_cost = actual_cost
		doc.actual_cost_recorded = 1
		if notes:
			doc.notes = notes
		doc.save(ignore_permissions=True)
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
			"actual_cost_recorded",
			"outcome_status",
			"fallback_used",
			"deidentified",
			"cost_currency",
		],
	)

	def cost(row):
		# A recorded flag, not the number: a Frappe Float is 0.0 when unset, so the
		# value alone cannot distinguish "the provider billed nil" from "nobody has
		# told us yet". Truthiness discarded the genuine zero; `is not None` never
		# fires at all.
		return row.actual_cost if row.actual_cost_recorded else (row.estimated_cost or 0)

	actual_rows = [r for r in rows if r.actual_cost_recorded]
	estimated_rows = [r for r in rows if not r.actual_cost_recorded and r.estimated_cost]
	unknown_rows = [r for r in rows if not r.actual_cost_recorded and not r.estimated_cost]

	# Cost per learner divides only what was attributed to a learner. Dividing the whole
	# total by the learners who happened to appear inflated it without bound, and read
	# as 0 for a period that cost real money when no row named anybody.
	learner_rows = [r for r in rows if r.learner]
	learners = {r.learner for r in learner_rows}
	learner_total = sum(cost(r) for r in learner_rows)
	total = sum(cost(r) for r in rows)

	# One currency per report or no total at all. Summing across currencies produces a
	# number that looks like money and is not, which matters more here than elsewhere.
	currencies = {r.cost_currency for r in rows if r.cost_currency}
	if len(currencies) > 1:
		return {
			"period_days": days,
			"interactions": len(rows),
			"total_cost": None,
			"currencies": sorted(currencies),
			"message": "Interactions span more than one currency; totals are not comparable.",
		}
	return {
		"period_days": days,
		"interactions": len(rows),
		"currency": currencies.pop() if currencies else None,
		"total_cost": round(total, 6),
		# Three states, not one flag: what the provider billed, what is only estimated,
		# and what is not known at all. A ledger with no cost fields used to report
		# total_cost 0.0, which a reader takes as "cheap" rather than "unknown".
		"total_actual": round(sum(cost(r) for r in actual_rows), 6),
		"total_estimated": round(sum(cost(r) for r in estimated_rows), 6),
		"interactions_with_no_cost_recorded": len(unknown_rows),
		"cost_is_partly_estimated": bool(estimated_rows),
		"learners": len(learners),
		"cost_per_learner": round(learner_total / len(learners), 6) if learners else None,
		"interactions_with_no_learner": len(rows) - len(learner_rows),
		"input_tokens": sum(r.input_tokens or 0 for r in rows),
		"output_tokens": sum(r.output_tokens or 0 for r in rows),
		"errors": len([r for r in rows if r.outcome_status in ("Error", "Timeout")]),
		# Counted apart from errors: a provider refusal is a content-policy event, and
		# on a clinical corpus it is the more interesting number of the two.
		"refusals": len([r for r in rows if r.outcome_status == "Refused"]),
		"fallbacks": len([r for r in rows if r.fallback_used]),
		# The number worth escalating: calls made without the caller asserting
		# de-identification. Should be zero, and is a fact rather than a guarantee.
		"without_deidentification_assertion": len([r for r in rows if not r.deidentified]),
		"by_model": sorted({r.model_id for r in rows if r.model_id}),
	}
