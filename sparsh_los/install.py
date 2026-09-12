# Copyright (c) 2026, SSSIHMS and contributors
# For license information, please see license.txt

import frappe

ROLES = ("Sparsh Learner", "Sparsh Reviewer")


def after_install():
	create_roles()


def create_roles():
	"""Create the engine's two roles. Idempotent — safe on reinstall."""
	for role_name in ROLES:
		if frappe.db.exists("Role", role_name):
			continue
		role = frappe.new_doc("Role")
		role.role_name = role_name
		role.desk_access = 1
		role.insert(ignore_permissions=True)

	frappe.db.commit()
