# Copyright (c) 2026, SSSIHMS and contributors
# For license information, please see license.txt

from frappe.model.document import Document

from sparsh_los.permissions import reject_identifiers


class SparshScenario(Document):
	def validate(self):
		# Same reason as Activity: authored case text is where a real patient's detail
		# is most likely to be pasted, and nothing checked it.
		reject_identifiers(self.title, self.context_description, self.expected_reasoning)
