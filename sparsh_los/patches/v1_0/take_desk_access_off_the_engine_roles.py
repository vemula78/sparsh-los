# Copyright (c) 2026, SSSIHMS and contributors
# For license information, please see license.txt

"""Take desk access off the engine's two roles, and put the holders back.

`create_roles` made both roles with `desk_access = 1`, and `create_roles` skips any
role that already exists -- so the flag could never be corrected by re-running it.

The flag is not cosmetic. Frappe recomputes `user_type` on every User save as
"System User" if any role the user holds has desk access, "Website User" otherwise.
Granting the programme owner `Sparsh Reviewer` so he could read three portal pages
promoted him to System User and opened the desk, alongside seventeen hospital apps
that have nothing to do with him. Every volunteer on the pilot roster would have been
promoted the same way.

Users are then re-saved so `user_type` is recomputed. Only users who hold one of these
roles and no other desk-access role are affected: `has_desk_access` looks at all of a
user's roles, so anyone who is a System Manager in their own right stays one.
"""

import frappe

ROLES = ("Sparsh Learner", "Sparsh Reviewer")


def execute():
	changed = []
	for role_name in ROLES:
		if not frappe.db.exists("Role", role_name):
			continue
		if not frappe.db.get_value("Role", role_name, "desk_access"):
			continue
		frappe.db.set_value("Role", role_name, "desk_access", 0, update_modified=False)
		changed.append(role_name)

	frappe.db.commit()

	demoted = []
	if changed:
		holders = frappe.get_all(
			"Has Role", filters={"role": ("in", ROLES)}, fields=["parent"], pluck="parent",
		)
		for user in sorted(set(holders)):
			if not frappe.db.exists("User", user):
				continue
			doc = frappe.get_doc("User", user)
			was = doc.user_type
			# The save is the point: validate() recomputes user_type from the roles.
			doc.save(ignore_permissions=True)
			doc.reload()
			if doc.user_type != was:
				demoted.append("%s: %s -> %s" % (user, was, doc.user_type))
		frappe.db.commit()

	print("take_desk_access_off_the_engine_roles: %d role(s) changed, %d user(s) recomputed"
		  % (len(changed), len(demoted)))
	for line in demoted:
		print("  %s" % line)
