# Copyright (c) 2026, SSSIHMS and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document


class SparshCohort(Document):
	def validate(self):
		# Editorial prose a reviewer writes; the only free text on this DocType.
		from sparsh_los.permissions import reject_identifiers

		reject_identifiers(self.title, self.notes)

		self._joined_on_is_the_servers()
		self._no_duplicate_members()
		self._set_active_keys()
		self._one_active_cohort_per_learner()

	def _joined_on_is_the_servers(self):
		"""`joined_on` records when the row was saved, not what the payload said.

		A read-only flag stops the Desk form; it does not stop an API client, and
		`frappe.client.save` accepts any value the DocType can hold. New rows take the
		server clock; rows that already existed keep the value they were saved with,
		whatever the payload carried this time.
		"""
		before = self.get_doc_before_save()
		earlier = {row.name: row.joined_on for row in before.members} if before else {}
		now = frappe.utils.now_datetime()
		for row in self.members:
			if row.name in earlier:
				row.joined_on = earlier[row.name]
			else:
				row.joined_on = now

	def _no_duplicate_members(self):
		seen = set()
		for row in self.members:
			if row.learner in seen:
				frappe.throw(_("{0} is listed twice in this cohort").format(row.learner))
			seen.add(row.learner)

	def _set_active_keys(self):
		"""Claim or release the key on every save, not on the status transition.

		The unique index on `Sparsh Cohort Member.active_key` is what actually holds
		the one-Active-cohort rule. It only holds if the key is right after *every*
		save: a member added to a cohort that is already Active is not a transition,
		and a key set only when the status moved would leave that row NULL and the
		index blind to it. So the key is derived from the current status each time.
		"""
		for row in self.members:
			row.active_key = row.learner if self.status == "Active" else None

	def _one_active_cohort_per_learner(self):
		"""The readable error. The unique index is the enforcement.

		Two saves that both read a learner as free will both pass this check; the
		second one's child-row write is then rejected by `unique_active_cohort_member`
		and surfaces as `frappe.UniqueValidationError`.
		"""
		if self.status != "Active" or not self.members:
			return

		learners = [row.learner for row in self.members]
		clash = frappe.db.sql(
			"""select m.learner, m.parent
			   from `tabSparsh Cohort Member` m
			   join `tabSparsh Cohort` c on c.name = m.parent
			   where m.learner in %(learners)s
			     and c.status = 'Active'
			     and c.name != %(name)s
			   limit 1""",
			{"learners": learners, "name": self.name},
			as_dict=True,
		)
		if clash:
			frappe.throw(
				_("{0} already belongs to the active cohort {1}; a learner follows one "
				  "pathway at a time").format(clash[0].learner, clash[0].parent)
			)
