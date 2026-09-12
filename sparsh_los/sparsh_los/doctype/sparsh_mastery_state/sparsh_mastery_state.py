# Copyright (c) 2026, SSSIHMS and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document

from sparsh_los.mastery import derive_state


class SparshMasteryState(Document):
	def validate(self):
		# Mastery is derived, never asserted. Only the recompute path may write it.
		if not frappe.flags.in_mastery_recompute:
			frappe.throw(
				_("Mastery State is derived from submitted Evidence and cannot be set directly"),
				frappe.ValidationError,
			)

		existing = frappe.db.get_value(
			self.doctype,
			{"learner": self.learner, "competency": self.competency, "name": ("!=", self.name)},
			"name",
		)
		if existing:
			frappe.throw(
				_("A Mastery State already exists for this learner and competency"),
				frappe.ValidationError,
			)

		self.state = derive_state(self.learner, self.competency)

	def on_trash(self):
		"""Deleting a derived state would leave submitted evidence unrepresented."""
		if not frappe.flags.in_mastery_recompute:
			frappe.throw(
				_("Mastery State is derived and cannot be deleted"), frappe.ValidationError
			)


def on_doctype_update():
	# The controller's duplicate check loses a race between concurrent recomputes.
	# Enforce the pair at the storage layer, where nothing can bypass it.
	frappe.db.add_unique(
		"Sparsh Mastery State", ["learner", "competency"], constraint_name="unique_learner_competency"
	)
