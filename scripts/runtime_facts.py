"""Read-only introspection of the installed app. Writes facts, changes nothing."""
import frappe, json, inspect

frappe.init(site="sparsh.localhost"); frappe.connect()
out = []
def sec(t): out.append("\n===== %s =====" % t)

sec("VERSIONS")
import frappe as f
out.append("frappe %s" % f.__version__)
out.append("installed apps: %s" % frappe.get_installed_apps())

sec("WHITELISTED METHODS FRAPPE ACTUALLY REGISTERED FOR sparsh_los")
# The real endpoint inventory: what the framework will dispatch, not what grep finds.
import frappe.utils.safe_exec  # noqa
seen = []
# Walking the top-level modules alone missed `Sparsh Certification Record.current()`,
# a whitelisted function defined in a doctype module -- and the resulting "21" was
# then read as evidence that the method was unreachable. Every module in the package
# is scanned now, so the inventory is the framework's, not a hand-kept list.
import importlib, pkgutil
import sparsh_los as _pkg

modules = []
for info in pkgutil.walk_packages(_pkg.__path__, prefix="sparsh_los."):
    try:
        modules.append(importlib.import_module(info.name))
    except Exception:
        continue
for m in modules:
    mod = m.__name__.replace("sparsh_los.", "", 1)
    for name, fn in vars(m).items():
        # Frappe records whitelisting by adding the function object to a module-level
        # set, not by setting an attribute on it — an attribute check silently finds none.
        if callable(fn) and fn in frappe.whitelisted:
            guest = fn in getattr(frappe, "guest_methods", set())
            methods = frappe.allowed_http_methods_for_whitelisted_func.get(fn, "?")
            sig = str(inspect.signature(fn))
            seen.append("sparsh_los.%s.%s%s  allow_guest=%s http=%s"
                        % (mod, name, sig, guest, methods))
out.extend(sorted(seen))
out.append("TOTAL whitelisted: %d" % len(seen))

sec("DOCTYPE PERMISSION ROWS AS INSTALLED")
rows = frappe.db.sql("""
 select parent, role, permlevel, `read`,`write`,`create`,`delete`,`submit`,`cancel`,`amend`,
        if_owner, report, export
 from `tabDocPerm` where parent like 'Sparsh %%' order by parent, permlevel, role""", as_dict=True)
for r in rows:
    flags = " ".join(k for k in ("read","write","create","delete","submit","cancel","amend",
                                 "if_owner","report","export") if r[k])
    out.append("%-34s permlevel=%s %-22s %s" % (r.parent, r.permlevel, r.role, flags))
out.append("TOTAL DocPerm rows: %d" % len(rows))

sec("FIELDS AT PERMLEVEL > 0")
for r in frappe.db.sql("""select parent, fieldname, fieldtype, permlevel from `tabDocField`
                          where parent like 'Sparsh %%' and permlevel > 0
                          order by parent, fieldname""", as_dict=True):
    out.append("%-34s %-28s %-12s permlevel=%s" % (r.parent, r.fieldname, r.fieldtype, r.permlevel))

sec("DATABASE INDEXES AND UNIQUE CONSTRAINTS ON Sparsh TABLES")
tables = [t[0] for t in frappe.db.sql("show tables like 'tabSparsh %'")]
for t in tables:
    for i in frappe.db.sql("show index from `%s`" % t, as_dict=True):
        if i.Key_name == "PRIMARY":
            continue
        out.append("%-40s %-34s unique=%s col=%s seq=%s" %
                   (t, i.Key_name, 0 if i.Non_unique else 1, i.Column_name, i.Seq_in_index))
out.append("TOTAL Sparsh tables: %d" % len(tables))

sec("HOOKS AS FRAPPE RESOLVED THEM")
h = frappe.get_hooks(app_name="sparsh_los")
for k in sorted(h):
    out.append("%s = %s" % (k, json.dumps(h[k], default=str)))

sec("SUBMITTABLE / TRACK-CHANGES FLAGS")
for r in frappe.db.sql("""select name, is_submittable, istable, track_changes, allow_rename
                          from tabDocType where module='Sparsh LOS' order by name""", as_dict=True):
    out.append("%-34s submittable=%s child=%s track_changes=%s allow_rename=%s" %
               (r.name, r.is_submittable, r.istable, r.track_changes, r.allow_rename))

sec("ROLES CREATED BY THIS APP")
for r in frappe.db.sql("""select name, disabled, is_custom from tabRole
                          where name like 'Sparsh %' order by name""", as_dict=True):
    out.append("%-30s disabled=%s is_custom=%s" % (r.name, r.disabled, r.is_custom))

sec("PROGRAMME READINESS (live)")
out.append(json.dumps(frappe.get_attr("sparsh_los.seed.programme_readiness")(), default=str, indent=1))

print("\n".join(out))
frappe.destroy()
