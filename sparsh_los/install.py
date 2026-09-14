# Copyright (c) 2026, SSSIHMS and contributors
# For license information, please see license.txt

import frappe

ROLES = ("Sparsh Learner", "Sparsh Reviewer")


def before_install():
	# DocType permission rows link to these roles, so they must exist before sync.
	create_roles()


def after_install():
	create_roles()


def create_roles():
	"""Create the engine's two roles. Idempotent — safe on reinstall."""
	for role_name in ROLES:
		if frappe.db.exists("Role", role_name):
			continue
		role = frappe.new_doc("Role")
		role.role_name = role_name
		# No desk access. Frappe recomputes `user_type` on every User save --
		# "System User" if any role the user holds has desk_access, "Website User"
		# otherwise -- so a desk-access role silently promotes the person holding it.
		# The programme owner was promoted that way by being made a reviewer, which
		# would have dropped him into the desk alongside seventeen unrelated hospital
		# apps, and the same would have happened to every volunteer on the pilot roster.
		# This app's entire interface is the portal pages (/practice, /queue,
		# /programme); nothing here is meant to be worked in the desk.
		role.desk_access = 0
		role.insert(ignore_permissions=True)

	frappe.db.commit()
