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
	# Two known false positives, both unlikely with current naming and neither worth
	# loosening the guard for: a model id spelled `ps4096...` or `ws2024...` matches the
	# MRN pattern, and one containing an @ with a dotted suffix matches the email
	# pattern. The rejection message will say "patient identifier", which would be
	# confusing; that is the trade.
	# Every caller-supplied free-text field on the row. The previous version said
	# exactly that and left out `cost_currency`, which is the same Data column on the
	# same exportable row. Counting them is the only way this comment stays true.
	reject_identifiers(
		notes, prompt_template, prompt_version, model_version, provider, model_id, cost_currency
	)

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
		doc.estimated_cost = estimated_cost or 0
		doc.estimated_cost_recorded = 1 if estimated_cost is not None else 0
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

	Narrow by convention, not by enforcement: `validate` gates on the flag alone, so any
	code holding it can rewrite any field. What makes an amendment safe here is
	`track_changes` on the DocType — without it an amendment leaves no prior value, no
	who and no when, which would be a worse trail than the delete-and-reinsert this
	exists to replace.
	"""
	if actual_cost is None:
		frappe.throw(_("Reconciling requires the cost the provider billed"))

	reject_identifiers(notes)

	previous = frappe.flags.in_sparsh_gateway
	frappe.flags.in_sparsh_gateway = True
	try:
		doc = frappe.get_doc("Sparsh Model Interaction", interaction)
		doc.actual_cost = actual_cost
		doc.actual_cost_recorded = 1
		if notes:
			# Appended, not replaced: overwriting destroys the only free-text record of
			# what the original call was.
			doc.notes = f"{doc.notes}\n{notes}" if doc.notes else notes
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
			"estimated_cost_recorded",
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
		if row.actual_cost_recorded:
			return row.actual_cost
		return row.estimated_cost if row.estimated_cost_recorded else 0

	actual_rows = [r for r in rows if r.actual_cost_recorded]
	# Same flag treatment for the estimate. Fixing the actual and leaving the estimate
	# on truthiness meant a deliberate estimate of 0.0 was reported as "nothing known".
	estimated_rows = [
		r for r in rows if not r.actual_cost_recorded and r.estimated_cost_recorded
	]
	unknown_rows = [
		r for r in rows if not r.actual_cost_recorded and not r.estimated_cost_recorded
	]

	# Cost per learner divides only what was attributed to a learner. Dividing the whole
	# total by the learners who happened to appear inflated it without bound, and read
	# as 0 for a period that cost real money when no row named anybody.
	learner_rows = [r for r in rows if r.learner]
	learners = {r.learner for r in learner_rows}
	learner_total = sum(cost(r) for r in learner_rows)
	total = sum(cost(r) for r in rows)

	# One currency per report or no total at all. Summing across currencies produces a
	# number that looks like money and is not, which matters more here than elsewhere.
	# A blank currency counts as another currency, not as "the same as the rest". It
	# used to be dropped from the set while its amount stayed in the total, so one
	# unlabelled row was summed into a figure reported as INR.
	# Only rows whose cost is actually summed. Taking every row meant one cost-less
	# entry with a blank currency blanked the totals for the whole period, though it
	# contributed nothing to them. And when every priced row is blank there is no
	# currency to report: a total in an unknown unit is not a single-currency total.
	priced = [r for r in rows if r.actual_cost_recorded or r.estimated_cost_recorded]
	currencies = {r.cost_currency for r in priced if r.cost_currency}
	unlabelled = any(not r.cost_currency for r in priced)
	# `bool(currencies) and unlabelled` made the all-blank case fall through: with every
	# priced row unlabelled the set is empty, so this was False and the report carried a
	# numeric total under `currency: None` -- an amount in an unknown unit, which is the
	# one thing the comment above says must not happen.
	mixed_currency = len(currencies) > 1 or unlabelled
	# Mixed currencies suppress the totals, not the report. Returning a different shape
	# meant every other figure -- including the count of calls made with no
	# de-identification assertion, the number worth escalating -- vanished exactly when
	# the ledger was messiest, and any caller reading them got a KeyError.
	return {
		"period_days": days,
		"interactions": len(rows),
		"currency": None if mixed_currency or not currencies else sorted(currencies)[0],
		"priced_interactions_without_a_currency": len([r for r in priced if not r.cost_currency]),
		"currencies": sorted(currencies),
		"mixed_currency": mixed_currency,
		"total_cost": None if mixed_currency else round(total, 6),
		# Three states, not one flag: what the provider billed, what is only estimated,
		# and what is not known at all. A ledger with no cost fields used to report
		# total_cost 0.0, which a reader takes as "cheap" rather than "unknown".
		"total_actual": None if mixed_currency else round(sum(cost(r) for r in actual_rows), 6),
		"total_estimated": None
		if mixed_currency
		else round(sum(cost(r) for r in estimated_rows), 6),
		"interactions_with_no_cost_recorded": len(unknown_rows),
		"cost_is_partly_estimated": bool(estimated_rows),
		"learners": len(learners),
		"cost_per_learner": None
		if (mixed_currency or not learners)
		else round(learner_total / len(learners), 6),
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
