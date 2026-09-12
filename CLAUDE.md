# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

`sparsh_los` is a Frappe v16 app: a domain-agnostic competency-learning engine, **not a course LMS**.
It records what a learner did, derives a competence state from that record, and requires a named
person to sign off anything safety-related. SAI SPARSH is its first content pack; nothing in a field
name or a validation may mention it.

The engine deliberately runs the LMS app (`lms`, installed on the same site) only as the Learn-stage
content layer. Do not add a dependency on it — `required_apps` is `["frappe"]` and must stay that
way.

## Working on a remote site

There is no local bench. The app runs on `erp.sssihms.org` in a Docker container. Every command goes
through the same chain:

```bash
ssh -i ~/Downloads/sssihms-web-vm2023_key.pem -p 2222 azureuser@20.219.253.136 \
  'docker exec -u frappe internal-backend-1 bash -lc "cd /home/frappe/frappe-bench && bench --site erp.sssihms.org <SUBCOMMAND>"'
```

Push local edits and run the full check:

```bash
./scripts/install_verify.sh
```

It tars the app into the container, pip-installs, installs on the site, and runs the harness.
`SKIP_VERIFY=1` proves plumbing only. `MIN_CHECKS` guards against a harness that silently runs
nothing — raise it whenever you add checks, or an empty `CHECKS` tuple reads as success.

Run the harness alone, or a single check:

```bash
bench --site erp.sssihms.org execute sparsh_los.verify.run
bench --site erp.sssihms.org execute sparsh_los.verify.check_critical_error_blocks
```

Tear down: `./scripts/uninstall.sh`. A clean uninstall-then-install is the real regression test for
schema work — run it after touching any DocType JSON.

### Two things that will waste your time

- `bench get-app <local path>` is **broken** on this bench (`AttributeError: 'App' object has no
  attribute 'org'`, bench 5.31). The script's `pip install -e` + `apps.txt` fallback is load-bearing.
- DocType JSON is only re-synced when its `modified` timestamp is newer than the database's. Editing
  a JSON without bumping `modified` means `bench migrate` silently ignores your change — and
  `on_doctype_update` (where unique indexes are created) never fires.

## Architecture

Sixteen modules over 13 top-level DocTypes and 6 child tables, all prefixed `Sparsh `.

| Module | Role |
|---|---|
| `runner.py` | The loop: instruction → response → evaluate → hint → retry → evidence |
| `mastery.py` | Derives state from Evidence; the critical-error gate; certification reconciliation |
| `orchestrator.py` | What the learner meets next and why; walks a Pathway |
| `review.py` | Queue of attempts nobody has judged; the path from one to Evidence |
| `escalation.py` | Learner questions with packaged context; the four reviewer dispositions |
| `certification.py` | Readiness: ready / blocked / insufficient, always with the reason |
| `refresher.py` | Refreshers by elapsed time or by a rule changing underneath the learner |
| `dashboard.py` | Learner view, supervisor view, heatmap, programme summary |
| `permissions.py` | Row scoping, role gates, the identifier guard, burst throttle |
| `seed.py` | Loads the Source-of-Truth Matrix and Starter Case Pack; programme readiness |
| `verify.py` | The acceptance harness |
| `www/practice.py`, `www/queue.py` | Learner page and reviewer queue |

Data flows one way: **Attempt → Evidence → Mastery State → Certification**. Nothing writes backwards.
`Sparsh Evidence.on_submit` / `on_cancel` are the only triggers for `recompute_mastery`, which is
also the only writer of Mastery State.

### Invariants — do not weaken these to make something pass

Each is covered by a check in `verify.py`. If a change makes one fail, the change is wrong until
proven otherwise.

- **Mastery is derived.** No role may create, write or delete a Mastery State, System Manager
  included. `validate()` and `on_trash` both refuse unless `frappe.flags.in_mastery_recompute`.
- **A safety error is cleared by a person, never by performing well afterwards.** A standing critical
  error blocks progression and certification until a submitted, approved `Sparsh Human Review` with
  `clears_critical_error` clears that specific evidence. Critical evidence cannot be cancelled at
  all — cleared or not — because cancelling it and then its review left nothing to reimpose the hold.
- **Assistance never falls.** Once a hint has been shown for an activity, every later answer on it is
  assisted. `runner.submit` does not accept `hint_level`; the server counts it from the attempt
  record. Demonstrating unaided competence requires a *different* activity.
- **Nobody judges their own work**, whatever roles they hold — no self-evidence, self-certification,
  self-review or self-answered escalation.
- **Learners cannot read `Sparsh Activity` or `Sparsh Scenario` at all.** Content reaches them only
  through `runner.start` / `runner.submit`. A permission level is not enough: a filter on a
  permlevel-1 field works as a prefix oracle, and a child table is its own queryable DocType — which
  is why the hint ladder and critical markers are text fields on Activity, not child tables.
- **One certificate stands per learner and competency**, forced Active at submission, suspended when
  its evidence stops supporting it, and a revoked one is never resurrected by later evidence.
- **Deterministic.** No network or model call anywhere at runtime. `ai_feedback_summary` exists for a
  future caller to fill.

### Trusted-code flags

Four flags mark "this is the engine acting, not a user": `in_mastery_recompute`,
`in_sparsh_runner`, `in_sparsh_certification`, `in_sparsh_maintenance`. They are set only inside
trusted server code and restored (not cleared) in a `finally`. Never set one from a whitelisted
function reachable by a request.

### Permissions

`permissions.is_restricted(user)` returns `not is_reviewer(user)` — restriction is the **default**.
It previously meant "holds the learner role and nothing privileged", which read as *unrestricted* for
an account with no role at all. Use `require_enrolment()` to gate an endpoint at all, and
`require_reviewer()` for anything that judges somebody. Every `@frappe.whitelist()` function is
callable by any logged-in user; an argument naming a learner is an access decision.

Row scoping for the six learner-owned DocTypes is in `hooks.permission_query_conditions` and
`has_permission`. Note that `has_permission` fires *before* `before_insert`, so a learner naming
another learner is rejected before any field correction runs.

## Content and clinical rules

Nothing clinical is validated. All 17 Source-of-Truth Matrix rules load as **Draft**, and the nine
Starter Case Pack activities load in **Human review** mode so the engine cannot score them. Check
the current position rather than assuming:

```bash
bench --site erp.sssihms.org execute sparsh_los.seed.programme_readiness
```

Do not give a case an `expected_response` or set it Deterministic until the rule behind it is
Validated by the programme owner. Never invent a clinical threshold, an answer key, or a scenario
value — if something is missing, the output says missing.

## Conventions

- Tabs, not spaces (Frappe convention; `ruff` config in `pyproject.toml`).
- stdlib + frappe only. No new dependencies.
- Fixture data in `verify.py` uses the `ZZV-` prefix and is deleted by `teardown()`. Delete fixture
  learners' records *before* their user accounts, or cancelling their evidence will try to recompute
  mastery for a learner who no longer exists.
- Comments explain why, not what. Do not write a comment asserting a guarantee you have not
  established — a reassuring comment is worse than a documented gap, because it stops the next
  person looking.

## History

`PLAN.md` holds the original plan and the decisions taken against it. `PLAN-REVIEW-LOG.md` is an
append-only record of seven independent audits and the disposition of every finding, including the
ones rejected with evidence. Read the log before re-litigating a design decision — several
obvious-looking "improvements" were tried and rejected there for reasons that are not obvious from
the code.
