# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

`sparsh_los` is a Frappe v16 app: a domain-agnostic competency-learning engine, **not a course LMS**.
It records what a learner did, derives a competence state from that record, and requires a named
person to sign off anything safety-related. SAI SPARSH is its first content pack; nothing in a field
name or a validation may mention it.

The engine deliberately runs the LMS app (`lms`, where a site has it) only as the Learn-stage
content layer. Do not add a dependency on it — `required_apps` is `["frappe"]` and must stay that
way.

## Working on the bench

Development happens on the **local bench** on this Mac (`frappe_docker`, container
`frappe_docker-backend-1`, site `sparsh.localhost`). The live hospital bench at
`erp.sssihms.org` is not a development environment — the programme owner made that a condition
of approval. Both scripts default to local; reaching the remote needs `TARGET=remote`, set
deliberately.

Push local edits and run the full check:

```bash
./scripts/install_verify.sh
```

It tars the app into the container, pip-installs, installs on the site, and runs the harness.
`SKIP_VERIFY=1` proves plumbing only. `MIN_CHECKS` guards against a harness that silently runs
nothing — raise it whenever you add checks, or an empty `CHECKS` tuple reads as success.

Run the harness alone, or a single check:

```bash
docker exec -u frappe frappe_docker-backend-1 bash -lc \
  'cd /home/frappe/frappe-bench && bench --site sparsh.localhost execute sparsh_los.verify.run'
docker exec -u frappe frappe_docker-backend-1 bash -lc \
  'cd /home/frappe/frappe-bench && bench --site sparsh.localhost execute sparsh_los.verify.check_critical_error_blocks'
```

Tear down: `./scripts/uninstall.sh`. A clean uninstall-then-install is the real regression test for
schema work — run it after touching any DocType JSON.

### Five things that will waste your time

- `bench get-app <local path>` is **broken** on both benches (`AttributeError: 'App' object has no
  attribute 'org'`, bench 5.31). The script's `pip install -e` + `apps.txt` fallback is load-bearing.
- `sites/apps.txt` on this bench has **no trailing newline**. Appending blind fuses the previous app
  name onto `sparsh_los` and the install then fails on a module named `sssihms_vmssparsh_los`. The
  script adds the newline first.
- The local compose stack runs **no rq worker**, so enqueued jobs accumulate for ever. Past 700 the
  framework refuses to enqueue at all and the harness dies with `QueueOverloaded` — which
  `bench execute` then masks as `NameError: name 'sparsh_los' is not defined`, because it falls back
  to `eval()` on the method string and reports *that* failure instead of the real one. Clear it with
  `docker exec frappe_docker-redis-queue-1 redis-cli flushall`. Never on the hospital bench.
- DocType JSON is only re-synced when its `modified` timestamp is newer than the database's. Editing
  a JSON without bumping `modified` means `bench migrate` silently ignores your change — and
  `on_doctype_update` (where unique indexes are created) never fires.
- Bumping `modified` is **necessary and not sufficient**: `install-app` is a no-op on an app that is
  already installed, so nothing re-reads the JSON at all. `install_verify.sh` runs `migrate` for
  exactly this reason. Without it a new column silently does not exist and every read of it fails
  with `Unknown column`. Note that `bench migrate` cannot be scoped to one app — there is no
  `--app` flag — so on a shared site it runs every installed app's pending patches.

## Architecture

Nineteen modules over 17 top-level DocTypes and 8 child tables, all prefixed `Sparsh `.
The acceptance harness is **82 checks**; raise `MIN_CHECKS` in `install_verify.sh` with it, or an
empty `CHECKS` tuple reads as success.

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
| `events.py` | The lightweight usage log: counts and states, never content |
| `gateway.py` | The model-interaction ledger: cost, versions, de-identification assertion. Makes no call |
| `seed.py` | Loads the Source-of-Truth Matrix and Starter Case Pack; programme readiness |
| `verify.py` | The acceptance harness |
| `www/practice.py`, `www/queue.py` | Learner page and reviewer queue |

Data flows one way: **Attempt → Evidence → Mastery State → Certification**, with two deliberate
exceptions. An approved `Sparsh Human Review` writes `critical_error_cleared` back onto submitted
Evidence, required by the safety-clearance rule. And `recompute_mastery` closes a satisfied
`Sparsh Refresher Assignment` before it derives, because an open assignment is an input to the
state and a learner who has answered it must not stay held by it.
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
- **A guard in `validate` is a guard on saving, and on nothing else.** Deletion does not pass
  through it, and neither do `db_set` or `frappe.db.set_value`. Anything worth freezing on save
  is worth an `on_trash` too — the answered escalation question was frozen field by field and
  could still be deleted outright.
- **A transition test is not a change test.** `became_current` and its like fire on a status
  moving, so an edit to a row that is *already* in that status is invisible to them. A Current
  resource's material fields are therefore refused rather than watched for.
- **A constraint added with its column is not retroactive.** Every row that predates the column
  holds NULL, and NULLs do not collide, so a unique index installs cleanly over data that already
  violates it. A new key column needs a backfill patch, and the patch reports what it cannot
  decide rather than guessing.
- **No unvalidated rule becomes production logic.** If an activity's competency links a rule
  that is not `Validated`, the runner refuses to auto-score it and routes to a reviewer. This
  was documentation-only until an audit showed a Deterministic activity against a Draft
  safety-critical rule scoring itself and moving Mastery.
- **Learner-only accounts cannot read `Sparsh Activity` or `Sparsh Scenario` at all.** Reviewers
  can, and Frappe unions permissions across roles, so a learner who is *also* a reviewer reads
  them by virtue of the reviewer role — the guarantee is about the learner role, not the person. Content reaches them only
  through `runner.start` / `runner.submit`. A permission level is not enough: a filter on a
  permlevel-1 field works as a prefix oracle, and a child table is its own queryable DocType — which
  is why the hint ladder and critical markers are text fields on Activity, not child tables.
- **One certificate stands per learner and competency**, forced Active at submission, suspended when
  its evidence stops supporting it, and a revoked one is never resurrected by later evidence.
- **Deterministic.** No network or model call anywhere at runtime. `ai_feedback_summary` exists for a
  future caller to fill. `gateway.py` records what a model call *cost* and what was asserted
  about it; it makes no call itself and imports no provider SDK. The real control is
  `pyproject.toml` declaring no third-party dependency. `check_no_module_imports_a_network_client`
  is a **tripwire for the careless case, not a proof** — it cannot see a dynamic import,
  `frappe.get_attr("requests.get")`, or Frappe's own request helpers without an import line. Its
  first version banned each module in one of its two spellings and would have missed
  `import openai` entirely. `Activity.evaluation_mode` declares six evaluator types, but only
  `Deterministic` is dispatched. Every unbuilt mode returns `Not Evaluated` and waits for a
  person; `Reflection` also returns `Not Evaluated` but waits for nobody — it is stored, kept out
  of `review.pending`, and refused by `record_evidence`, because a reflection is the learner's own
  writing and not work awaiting a verdict. An unbuilt mode must never fall through to the string
  comparison, and a response matching a critical marker still escalates to a person in every mode.
- **Content is versioned apart from the engine.** `Sparsh Learning Resource` carries its own
  version and supersession lineage, and a competency references many resources. Superseding
  content marks affected learners `Refresh Due`; it never rewrites prior evidence or
  certification history.
- **The event log is a log.** No role may write one, `events.emit` never raises, and no learner
  response or answer key goes into a `detail` string.
- **A guard may not read a field the caller can write.** Whoever is acting is a fact about the
  request — `frappe.session.user` — never about the payload. Two audits running broke the
  self-answer rule this way: first by reading `answered_by` off the document, then by comparing
  against a `learner` field the same save could change. A question cannot change hands, and an
  answered one is immutable in full rather than in the fields someone thought to list.
- **A uniqueness rule that a query enforces is not enforced.** Two transactions both read a free
  slot and both take it. `Sparsh Certification Record.standing_key` and
  `Sparsh Learning Resource.current_key` are held only while the row is standing or current, NULL
  otherwise — NULLs do not collide — with a unique index behind each. The query stays for the
  readable error. Whatever sets such a key must also cover the ordinary edit: clearing it on every
  validate left a Current row holding nothing and the index blind to a second Current version.
- **A queue filters before it limits.** `review.pending` applies eligibility in the query, because
  taking the oldest N rows and then discarding the ineligible ones returns an empty queue while
  work waits behind it, and says nothing about having truncated.

### Trusted-code flags

Six flags mark "this is the engine acting, not a user": `in_mastery_recompute`,
`in_sparsh_runner`, `in_sparsh_certification`, `in_sparsh_maintenance`, `in_sparsh_event`,
`in_sparsh_gateway`. They are set only inside
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
docker exec -u frappe frappe_docker-backend-1 bash -lc \
  'cd /home/frappe/frappe-bench && bench --site sparsh.localhost execute sparsh_los.seed.programme_readiness'
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
  person looking. Nine separate defects here were first described accurately by the comment beside
  the code that did not implement them.
- `_raises` is for `frappe.ValidationError`, `_refused` for `frappe.PermissionError` — the two are
  not related by inheritance on this version, so a permission refusal reaching `_raises` fails the
  check it should satisfy. Pass `expect=` always: without it a check passes on a refusal from any
  layer, which is how several of them came to test nothing.
- **Assert on the fixture by name, or on a delta. Never on a floor.** `>= 1` is satisfied by another
  check's leftovers on a shared site. Three checks were found hollow this way, including one whose
  fixture never produced the state it claimed to test.
- **A fix is not verified until its check has been shown to fail with the fix reverted.** This is
  the single most productive rule in the project; it has exposed a check that tested one DocType
  under a name covering six, one that passed on ambient state, and several that asserted nothing at
  all.

## History

`PLAN.md` holds the original plan and the decisions taken against it. `PLAN-REVIEW-LOG.md` is an
append-only record of twenty review rounds — five of them genuinely independent, run outside this
toolchain — and the disposition of every finding, including the ones rejected with evidence. The
independent rounds found what the in-house ones could not: the blocker that Draft rules did not
prevent automatic scoring, then, twice running, that a fix had survived its own new check, and
then that three guarantees held on the save path and nowhere else.
**Their most valuable findings have been about the harness, not the app, in every round.** Read the log before re-litigating a design decision — several
obvious-looking "improvements" were tried and rejected there for reasons that are not obvious from
the code.
