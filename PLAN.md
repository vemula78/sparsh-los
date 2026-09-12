# sparsh_los — Advisor Plan (Fable 5) + Orchestrator Decisions

Task: scaffold the `sparsh_los` Frappe v16 app (SAI SPARSH Learning Operating System core engine)
and prove it installs on the demo site `erp.sssihms.org`.

## Orchestrator decisions on the advisor's open questions

| # | Question | Decision |
|---|---|---|
| 1 | DocType name prefix | **Prefix all 12 top-level DocTypes and all child tables with `Sparsh `.** Live check on 12-Sep-2026 found zero collisions, but 17 apps are installed and more will be; the prefix costs nothing and removes the class of failure entirely. |
| 2 | Hyphen in `Source-of-Truth Rule` | **Rename to `Sparsh Source of Truth Rule`.** A hyphen produces an invalid Python class name and the controller silently fails to load. |
| 3 | Rule versioning identity | **`autoname: format:{rule_id}-v{version}`**, `rule_id` non-unique, `supersedes` self-Link. Each version is its own immutable document. |
| 4 | `learner` Link target | **`User`.** Always present; Linking to `Employee` couples the engine to hrms and breaks domain-agnosticism. |
| 5 | Submittable | **`Sparsh Evidence` and `Sparsh Certification Record` submittable; `Sparsh Attempt` not.** Evidence must be immutable once recorded; attempts are high-volume. |
| 6 | Mastery State naming | **`hash`**, with (learner, competency) uniqueness enforced in `validate()`. Email addresses in `name` are legal but unreadable. |
| 7 | `Refresh Due` transition | **Out of scope for this scaffold.** The state exists in the Select; no scheduler hook, no `refresh_interval_days`. Deferred to build-brief Phase 7. |
| 8 | Roles | **Create `Sparsh Learner` and `Sparsh Reviewer` in `after_install`.** Permissions are part of the safety design, not decoration. |
| 9 | Install mechanism | **`bench get-app <local path>` first, fall back to `env/bin/pip install -e` + `apps.txt` append.** Either is accepted. |

## Advisor plan (verbatim, Fable 5)

### 1. Recommended approach
Single Frappe module `Sparsh LOS` (one entry in `modules.txt`), 12 top-level DocTypes plus child
tables, hand-written DocType JSON (v16 shape), one Python controller per DocType only where logic
is needed (Attempt, Evidence, Mastery State, Source of Truth Rule, Certification Record), and one
pure-Python module `sparsh_los/mastery.py` holding the deterministic derivation/gating.

Mastery State is rewritten only from Evidence `on_submit`/`on_cancel` (Evidence is
`is_submittable: 1`). Attempt is not submittable but `before_insert` snapshots `rule_version` from
the linked rule and, when `critical_error=1`, forces a failing outcome. Users cannot set
`Mastery State.state`: the field is `read_only: 1` and `validate()` recomputes from evidence and
raises unless `frappe.flags.in_mastery_recompute` is set.

Rule versioning: `version` Int, `supersedes` self-Link, `status=Superseded` set on the old doc when
a new doc names it. Enforce in `validate()` that a Superseded rule cannot be un-superseded and that
the `supersedes` target has a lower version. Not `amended_from` (requires submittable, produces
`-1` suffixes that hurt readability).

**Rejected alternative 1:** Attempt and Evidence both submittable with `amended_from`, mastery
derived by a scheduler job. Rejected because scheduler-driven derivation is not synchronously
testable via `bench execute`, makes the acceptance check non-deterministic on a Docker site where
the scheduler may be disabled, and doubles DocType complexity. Event-driven synchronous recompute
is smaller and provable.

**Rejected alternative 2:** building on the installed `lms` app with Links to `LMS Course` /
`Course Lesson`. Rejected because a cross-app hard dependency in `required_apps` ties install order
and uninstall to `lms`; linked lessons are instead a child table with a `Data` reference so the
engine stays domain- and app-agnostic.

### 2. Files to read before writing
Inside the container via ssh + `docker exec`:
- `apps/frappe/frappe/core/doctype/doctype/doctype.json` — authoritative v16 DocType-level keys.
- `apps/frappe/frappe/core/doctype/docfield/docfield.json` — accepted DocField keys and fieldtypes.
- `apps/frappe/frappe/model/__init__.py` — `default_fields`, `child_table_fields`, reserved names.
- `apps/frappe/frappe/core/doctype/doctype/doctype.py` — `validate_fields`, `validate_autoname`,
  `check_if_fieldname_conflicts_with_methods`, illegal fieldname sets.
- A small installed in-house app for v16 scaffold shape: `pyproject.toml`, `hooks.py`,
  `modules.txt`, `__init__.py` tree, one DocType JSON.
- `apps/lms/lms/lms/doctype/lms_quiz_submission/*.json` — realistic v16 JSON with Link, child
  table, Select, and the `row_format` / `states` / `links` / `sort_field` keys.
- `apps/frappe/frappe/installer.py` and `frappe/commands/site.py` — install/uninstall semantics.
Locally: the Master Build Guide (semantics of the loop, mastery states, hint ladder, critical
errors) and the Source-of-Truth Matrix (column headers only — option lists). No content from
either may be seeded as fixture data.

### 3. Failure modes
**Schema drift.** Every JSON needs `doctype`, `name`, `module` (exact case), `engine: InnoDB`,
`field_order` listing every fieldname exactly once, `fields`, `permissions`, `sort_field`,
`sort_order`, `states: []`, `links: []`, `actions: []`, `idx`, `owner`, `creation`, `modified`.
`field_order` vs `fields` mismatch is the most common install failure. Sync only reimports when
JSON `modified` > DB `modified` — bump it when editing or `bench migrate` silently skips.

**Child tables.** `istable: 1` plus `editable_grid: 1`, `permissions: []`, no `autoname`. Parent
`Table` field `options` must name the child DocType exactly. Child names must be globally unique
across all installed apps.

**Links.** `options` naming a non-existent DocType fails install with "Options must be a valid
DocType". Self-referencing Links are fine (two-pass sync). `Dynamic Link` requires a sibling Link
to `DocType`.

**Select.** Options are newline-separated strings, not arrays; any `default` must match an option
exactly; a leading blank line gives an empty default.

**`fetch_from` trap.** Tempting for `Attempt.rule_version`, but it re-fetches on every save and
would silently rewrite the historical version. Set it once in Python and mark `read_only`.

**Naming.** `autoname: field:x` requires x unique+reqd. Avoid `autoincrement` (BIGINT `name`
breaks Link assumptions). Validate `rule_id` as `[A-Z0-9-]+` — `/` or `#` in a `field:` autoname
produces broken URLs.

**Reserved fieldnames.** `name, owner, creation, modified, modified_by, docstatus, idx, parent,
parentfield, parenttype, doctype, _user_tags, _comments, _assign, _liked_by`. `owner` is reserved —
the Rule spec's "owner" must become `rule_owner`. Avoid bare `type` (use `activity_type`) and bare
`timestamp` (use `attempted_at`). Fieldnames colliding with `Document` methods (`save`, `insert`,
`get`, `set`, `validate`, `meta`, `flags`, `run_method`) are rejected outright.

**Module registration.** `modules.txt` must exactly equal `module` in every JSON, and `__init__.py`
must exist in `sparsh_los/sparsh_los/`, `.../doctype/`, and every DocType folder — otherwise the
controller import fails at first form load, not at install, which is a late and confusing failure.
Folder name is `frappe.scrub(name)`; the controller class is CamelCase of the name with no hyphens.

**Install mechanics.** `docker cp` lands files as root — `chown -R frappe:frappe` after. `bench
install-app` does not pip-install: either `bench get-app <path>` or `env/bin/pip install -e` plus an
`apps.txt` entry. `sparsh_los/__init__.py` must define `__version__` or flit fails. `patches.txt`
must exist even if empty. After install, `bench clear-cache` and restart the backend or desk 404s
the new DocTypes. Uninstall for rerun: `uninstall-app --yes --no-backup --force`.

**Permissions.** Submittable DocTypes need explicit `submit`/`cancel` in permissions or nobody can
submit. Mastery State must not be user-writable even for System Manager — enforced in `validate()`.

**Hard-rule enforcement.** Critical error blocks progression: in `derive_state`, if any Evidence for
that competency with `critical_error=1` is newer than the latest independent pass, the state cannot
exceed `Practising`. Cancelling Evidence must trigger recompute or mastery goes stale. Nothing in
this app may call out to a network service.

### 4. Acceptance check
Static, local: every DocType JSON parses, `module == "Sparsh LOS"`, `field_order` set-equals the
fieldnames with no duplicates, child tables carry empty permissions, `python3 -m compileall` clean,
`modules.txt` exact.

Install, on the site: app appears in `bench list-apps`; `select count(*) from tabDocType where
module='Sparsh LOS' and istable=0` returns exactly 12.

Behavioural, `bench execute sparsh_los.verify.run`, printing `PASS`/`FAIL` per check and a final
`RESULT passed=N failed=M`:
1. `rule_version_snapshot` — Rule v1, Attempt A1 → `rule_version==1`; Rule v2 supersedes v1 → v1
   status becomes Superseded; reload A1 → still 1; A2 against v2 → 2.
2. `mastery_not_directly_settable` — inserting a Mastery State with `state="Mastered"` raises.
3. `mastery_derived_from_evidence` — submitting a passing independent Evidence creates exactly one
   Mastery State for (learner, competency) in {Demonstrated, Mastered}.
4. `critical_error_blocks` — a critical-error Evidence caps the state below Demonstrated and blocks
   certification for that competency.
5. `evidence_cancel_recomputes` — cancelling the passing Evidence regresses the state.
6. `no_pii_fields` — no fieldname or label matches `patient|mrn|uhid|dob|aadhaar|phone|address`.
7. `no_domain_strings` — no label, option or description contains "SPARSH" or "SAI".
8. `cleanup` — fixtures deleted; Attempt/Evidence/Mastery State counts return to zero.
Expected: `RESULT passed=8 failed=0`.

Teardown and idempotency: uninstall → DocType count 0 → reinstall → `RESULT passed=8 failed=0`
a second time.

### 5. Work split
**Builder A (Opus) owns only:** `sparsh_los/sparsh_los/doctype/**` (every DocType folder: `.json`,
`.py`, `__init__.py`), `sparsh_los/mastery.py`, `sparsh_los/verify.py`, `sparsh_los/install.py`.
Hands Builder B the DocType count for the README and the SQL assertion.

**Builder B (Sonnet) owns only:** `pyproject.toml`, `README.md`, `license.txt`, `.gitignore`,
`sparsh_los/__init__.py`, `sparsh_los/hooks.py`, `sparsh_los/modules.txt`, `sparsh_los/patches.txt`,
`sparsh_los/sparsh_los/__init__.py`, `sparsh_los/sparsh_los/doctype/__init__.py` (this one file
only), `scripts/install_verify.sh`, `scripts/uninstall.sh`. No `config/` directory — `desktop.py`
is obsolete in v16.

**Shared-file rule:** `hooks.py` is B's alone. A must not add `doc_events` — all event logic lives
in DocType controllers. If A needs a hook line, A reports it and B adds it.

**Sequencing:** B's scaffold is installable with zero DocTypes. B proves the plumbing first with
`SKIP_VERIFY=1`; A's DocTypes then drop in and the full run happens once.
