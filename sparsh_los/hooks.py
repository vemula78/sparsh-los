from . import __version__ as app_version

app_name = "sparsh_los"
app_title = "Sparsh Learning OS"
app_publisher = "SSSIHMS"
app_description = "Domain-agnostic competency-learning engine"
app_email = "vemula78@gmail.com"
app_license = "MIT"
required_apps = ["frappe"]

# Installation
# ------------

# Roles are created before DocType sync: the permission rows in the DocType JSON
# link to them, and a missing Role fails the install.
before_install = "sparsh_los.install.before_install"
after_install = "sparsh_los.install.after_install"

# Where each role lands after signing in
# --------------------------------------
# Without this, everyone arrives at `/me`, Frappe's default portal page, which says
# nothing about this programme -- the programme owner signing in to review the rules
# would have had to be told a URL to type. A reviewer lands on the programme dashboard,
# a learner on their practice page.
#
# For somebody holding both roles the winner is decided by the order `frappe.get_roles()`
# returns, not by the order written here: Frappe walks the user's roles and takes the
# first that appears in this dict. Both destinations are readable by anyone holding
# either role, so the worst case is landing on the other one and following a link.
role_home_page = {
	"Sparsh Reviewer": "programme",
	"Sparsh Learner": "practice",
}

# Permissions
# -----------
# DocType permissions grant a role access to a kind of record; these narrow a
# learner to their own rows.

permission_query_conditions = {
	"Sparsh Attempt": "sparsh_los.permissions.attempt_query",
	"Sparsh Evidence": "sparsh_los.permissions.evidence_query",
	"Sparsh Mastery State": "sparsh_los.permissions.mastery_state_query",
	"Sparsh Certification Record": "sparsh_los.permissions.certification_record_query",
	"Sparsh Escalation Question": "sparsh_los.permissions.escalation_question_query",
	"Sparsh Refresher Assignment": "sparsh_los.permissions.refresher_assignment_query",
}

has_permission = {
	"Sparsh Attempt": "sparsh_los.permissions.has_permission",
	"Sparsh Evidence": "sparsh_los.permissions.has_permission",
	"Sparsh Mastery State": "sparsh_los.permissions.has_permission",
	"Sparsh Certification Record": "sparsh_los.permissions.has_permission",
	"Sparsh Escalation Question": "sparsh_los.permissions.has_permission",
	"Sparsh Refresher Assignment": "sparsh_los.permissions.has_permission",
}

# Scheduled jobs
# --------------
# Time-based refreshers. The same function is callable directly, so the acceptance
# harness does not depend on the scheduler running.

scheduler_events = {
	"daily": ["sparsh_los.refresher.daily"],
}
