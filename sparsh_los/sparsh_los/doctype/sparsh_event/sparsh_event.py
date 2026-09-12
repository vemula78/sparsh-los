# Copyright (c) 2026, SSSIHMS and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document


class SparshEvent(Document):
	def validate(self):
		"""An event is a fact about something that happened; it is not editable.

		No role holds write or create on this DocType, so the only way in is
		`events.emit`, which sets the flag below. The guard is here as well because a
		System Manager with a console is not stopped by a DocPerm.
		"""
		if not frappe.flags.in_sparsh_event:
			frappe.throw(_("Events are written by the engine, not edited"))

	def on_trash(self):
		if not frappe.flags.in_sparsh_maintenance:
			frappe.throw(_("An event log that can be edited is not a log"))


def on_doctype_update():
	# The two questions this log is asked: what happened to this learner, and how much
	# of this event type happened. Both scan, so both get an index.
	frappe.db.add_index("Sparsh Event", ["learner", "occurred_at"])
	frappe.db.add_index("Sparsh Event", ["event_type", "occurred_at"])
