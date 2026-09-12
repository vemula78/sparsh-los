# Copyright (c) 2026, SSSIHMS and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document


class SparshModelInteraction(Document):
	def validate(self):
		if not frappe.flags.in_sparsh_gateway:
			frappe.throw(_("Model interactions are recorded by the gateway, not edited"))

	def on_trash(self):
		if not frappe.flags.in_sparsh_maintenance:
			frappe.throw(_("The cost and privacy ledger cannot be edited"))


def on_doctype_update():
	# The two questions this ledger is asked: what did this learner cost, and what did
	# this model cost over a period.
	frappe.db.add_index("Sparsh Model Interaction", ["learner", "occurred_at"])
	frappe.db.add_index("Sparsh Model Interaction", ["model_id", "occurred_at"])
