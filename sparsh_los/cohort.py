# Copyright (c) 2026, SSSIHMS and contributors
# For license information, please see license.txt

"""Which cohort a learner is in, and so which pathway they follow.

A cohort is a group of learners walking one pathway together; the pilot is one.
The engine asks one question of it -- "which pathway does this learner follow?" --
and the answer has to be single-valued, which is why a learner may belong to at
most one Active cohort. That rule is held by a unique index on
`Sparsh Cohort Member.active_key`; see the controller.
"""

import frappe
from frappe import _

from sparsh_los.permissions import require_reviewer


def _active_memberships(learner):
	return frappe.db.sql(
		"""select c.name, c.pathway
		   from `tabSparsh Cohort Member` m
		   join `tabSparsh Cohort` c on c.name = m.parent
		   where m.learner = %(learner)s and c.status = 'Active'
		   order by c.name""",
		{"learner": learner},
		as_dict=True,
	)


def _the_active_cohort(learner):
	"""The single Active cohort, or None. Two is refused, not resolved.

	Server-side writes (`db.set_value` on a cohort's status) bypass the controller
	and so the key; if that ever produces two Active memberships this raises rather
	than letting `order by` decide which pathway the learner is on.
	"""
	rows = _active_memberships(learner)
	if not rows:
		return None
	if len(rows) > 1:
		frappe.throw(
			_("{0} belongs to more than one active cohort ({1}); resolve this before "
			  "the engine can pick a pathway").format(learner, ", ".join(r.name for r in rows))
		)
	return rows[0]


def cohort_for(learner):
	"""Name of the learner's Active cohort, else None. Server-side callers only."""
	row = _the_active_cohort(learner)
	return row.name if row else None


def pathway_for(learner):
	"""Pathway of the learner's Active cohort, else None. Server-side callers only.

	None also when the cohort has no pathway set; the caller decides what a learner
	with no pathway meets next.
	"""
	row = _the_active_cohort(learner)
	return row.pathway if row and row.pathway else None


def member_learners(cohort):
	"""Learner ids of a cohort's members, any status. Not whitelisted."""
	return frappe.get_all(
		"Sparsh Cohort Member",
		filters={"parent": cohort, "parenttype": "Sparsh Cohort"},
		pluck="learner",
		order_by="idx asc",
	)


@frappe.whitelist()
def members(cohort):
	"""The membership list with join dates. A reviewer action: it names learners."""
	require_reviewer()
	if not frappe.db.exists("Sparsh Cohort", cohort):
		frappe.throw(_("No such cohort"), frappe.DoesNotExistError)

	return frappe.get_all(
		"Sparsh Cohort Member",
		filters={"parent": cohort, "parenttype": "Sparsh Cohort"},
		fields=["learner", "joined_on"],
		order_by="idx asc",
	)
