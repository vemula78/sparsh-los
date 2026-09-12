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

after_install = "sparsh_los.install.after_install"
