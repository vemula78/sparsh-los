# Copyright (c) 2026, SSSIHMS and contributors
# For license information, please see license.txt

"""Who the masthead says this belongs to.

The institute's name and campus were written into three templates. That is the same
coupling the engine refuses everywhere else: a page compiled with one deployment's
identity in it cannot serve another without editing HTML, and the whole architectural
claim of this app is that the engine is reusable and only the content pack changes.

The name is not a *programme* name -- the domain scanner forbids those in code, and this
is not one -- but the argument for keeping it out of the templates is the same.

Set `sparsh_branding` in site_config.json to override. The default is the deployment
this was written for, because a masthead that renders blank is worse than one that
renders a name somebody has to change.
"""

import frappe

DEFAULT = {
	"institute": "Sri Sathya Sai Institute of Higher Medical Sciences",
	"campus": "Prasanthigram | Whitefield",
	"tagline": "Loka Samastha Sukhino Bhavantu",
}


def masthead():
	"""The institute line, campus line and tagline for a page header."""
	configured = frappe.conf.get("sparsh_branding") or {}
	if not isinstance(configured, dict):
		# A malformed site_config value should not take the header down.
		configured = {}
	return {key: configured.get(key) or value for key, value in DEFAULT.items()}
