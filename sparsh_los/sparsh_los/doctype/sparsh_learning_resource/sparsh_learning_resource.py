# Copyright (c) 2026, SSSIHMS and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document


class SparshLearningResource(Document):
	def validate(self):
		if self.supersedes == self.name:
			frappe.throw(_("A resource cannot supersede itself"))

		self._check_lineage()
		self._material_changes_need_a_new_version()

	# What the learner is actually sent to study. A change to any of these is a change
	# of material, however the title reads.
	MATERIAL_FIELDS = ("resource_id", "version", "resource_type", "url", "lms_lesson",
					   "file_reference", "source")

	def _material_changes_need_a_new_version(self):
		"""A Current resource's material is fixed; changing it means a new version.

		The refresher hook fires on `became_current` -- a status transition. Editing the
		`url` of a resource that is already Current is not a transition, so the edit went
		through, `current_key` was preserved, and nothing marked the learners who had
		already studied the old material Refresh Due. Their competence state then
		asserted mastery of material the row no longer points at.

		Supersession is the path the programme owner specified for exactly this: the old
		version stays, earlier evidence stays with it, and its readers become Refresh
		Due. So the edit is refused rather than made to trigger a refresh -- a refresh
		without a preserved predecessor still loses the record of what was studied.

		Editorial fields -- title, notes, effective_date, status, supersedes -- are not
		material and stay editable.
		"""
		if self.is_new():
			return

		before = self.get_doc_before_save()
		if not before or before.status != "Current":
			return

		changed = [f for f in self.MATERIAL_FIELDS if self.get(f) != before.get(f)]
		if changed:
			frappe.throw(
				_("{0} is Current: change {1} by publishing a new version that supersedes it, "
				  "so earlier evidence keeps the material it was earned on").format(
					self.name, ", ".join(changed)
				)
			)

	def _check_lineage(self):
		"""Supersession is a claim about the same material, so check that it is.

		`on_update` retires the predecessor and marks its readers Refresh Due on the
		strength of this link alone. With only the self-supersession check, one resource
		could retire an unrelated one and send those learners back to study material that
		had not changed, and several versions could be Current at once with no way to say
		which one a learner is meant to be reading.
		"""
		if self.supersedes:
			before = frappe.db.get_value(
				"Sparsh Learning Resource", self.supersedes, ["resource_id", "version"], as_dict=True
			)
			if not before:
				frappe.throw(_("The superseded resource does not exist"))
			if before.resource_id != self.resource_id:
				frappe.throw(
					_("A resource may only supersede another version of itself ({0}, not {1})").format(
						self.resource_id, before.resource_id
					)
				)
			if (self.version or 0) <= (before.version or 0):
				frappe.throw(
					_("Version {0} cannot supersede version {1}: a successor comes after").format(
						self.version, before.version
					)
				)

		if self.status != "Current":
			# Only a Current version occupies the key; NULLs do not collide, so every
			# superseded and draft version is free to exist alongside.
			self.current_key = None
			return

		# The key is claimed in `on_update`, after the predecessor has released it --
		# claiming it here would collide with the version this one is replacing, which
		# is still Current until validation passes.
		#
		# Only while *becoming* Current, though. Clearing it unconditionally meant any
		# ordinary edit to an already-Current resource -- a corrected title -- dropped
		# the key and `on_update` never restored it, because that is not a transition.
		# The row stayed Current holding nothing, and the next version could claim the
		# key without the unique index noticing two Current versions.
		before = self.get_doc_before_save()
		if before and before.status == "Current":
			self.current_key = self.resource_id
		else:
			self.current_key = None

		# The predecessor is still Current while this one is being saved -- `on_update`
		# demotes it only after validation passes -- so the version being superseded is
		# not a clash. Anything else Current under the same resource_id is.
		seen = [self.name]
		if self.supersedes:
			seen.append(self.supersedes)
		clash = frappe.get_all(
			"Sparsh Learning Resource",
			filters={"resource_id": self.resource_id, "status": "Current", "name": ("not in", seen)},
			limit=1,
			pluck="name",
		)
		if clash:
			frappe.throw(
				_("{0} is already the current version of {1}").format(clash[0], self.resource_id)
			)



	def on_update(self):
		"""Becoming Current retires the predecessor and makes its readers stale.

		The programme owner's requirement is explicit: when an approved resource
		changes materially, earlier evidence stays as it is and the competency is
		marked Refresh Due instead. Nothing here rewrites a learner's history.
		"""
		before = self.get_doc_before_save()
		became_current = self.status == "Current" and (not before or before.status != "Current")
		if not became_current:
			return

		if not self.supersedes:
			# A first version has no predecessor to demote, but it still holds the key.
			self.db_set("current_key", self.resource_id)
			return

		# The predecessor releases the key as it is demoted, and this version claims it
		# immediately after. Two successors racing the same predecessor both reach this
		# point, and the second one's claim is rejected by the unique index -- which is
		# the only place that guarantee can be enforced, because the check in `validate`
		# reads a state both transactions still see as free.
		frappe.db.set_value(
			"Sparsh Learning Resource",
			self.supersedes,
			{"status": "Superseded", "current_key": None},
		)
		self.db_set("current_key", self.resource_id)

		from sparsh_los.refresher import on_resource_superseded

		on_resource_superseded(self.supersedes)


def on_doctype_update():
	# One Current version per resource_id, enforced where it cannot be raced.
	frappe.db.add_unique(
		"Sparsh Learning Resource", ["current_key"], constraint_name="unique_current_resource"
	)
