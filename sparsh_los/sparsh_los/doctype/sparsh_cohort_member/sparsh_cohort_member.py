# Copyright (c) 2026, SSSIHMS and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document


class SparshCohortMember(Document):
	pass


def on_doctype_update():
	# One Active cohort per learner, enforced where it cannot be raced. `active_key`
	# is the learner while the parent is Active and NULL otherwise, so members of
	# Draft and Closed cohorts never collide.
	frappe.db.add_unique(
		"Sparsh Cohort Member", ["active_key"], constraint_name="unique_active_cohort_member"
	)
	# No learner twice in one cohort, whatever the cohort's status.
	frappe.db.add_unique(
		"Sparsh Cohort Member", ["parent", "learner"], constraint_name="unique_cohort_learner"
	)
