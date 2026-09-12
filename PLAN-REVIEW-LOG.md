# PLAN-REVIEW-LOG — sparsh_los scaffold

Append-only. Never edit a past entry.

## 2026-09-12 · Plan recorded
- PLAN.md sha256: `50553ad04e854e932a6f5895f16a726f2cea756223df02dff73ec2495ea27274`
- Advisor: Fable 5 (Plan agent). Nine open questions resolved by orchestrator; decisions table at
  head of PLAN.md.
- Live fact checked before recording: zero DocType name collisions on erp.sssihms.org for the 12
  proposed names; `Sparsh%` namespace empty; developer_mode off; scheduler active.
- Builders dispatched: Opus (doctype/**, mastery.py, verify.py, install.py),
  Sonnet (scaffold, hooks, scripts/).

## 2026-09-12 · Build, audit and adjudication
- Plan hash audited against: `50553ad04e854e932a6f5895f16a726f2cea756223df02dff73ec2495ea27274` (unchanged).
- Baseline commit `660e78e` — acceptance `RESULT passed=6 failed=2`.
- Final commit `9d1b3ff` — acceptance `RESULT passed=9 failed=0`, twice consecutively.

### Codex tier
Intended `gpt-5.6-astra` (safety-gate logic, cross-file interaction, 2558 LOC). **Not available on
this ChatGPT-account Codex** — `invalid_request_error`. Audit ran at `gpt-5.6-sol`, one tier below
what the diff warranted. Audit depth is correspondingly lower; treat coverage as partial.

### Builder C (Codex terra) — failed
Dispatched to fix the two failing checks. Ran ~25 minutes, consumed 0.07s CPU, produced zero bytes
of output and zero file changes. Hung, killed. The work was done by the orchestrator instead.

### Findings disposition
CONFIRMED AND FIXED
- B2 blocker — Evidence could cite a critical-error Attempt while declaring `critical_error=0`,
  `outcome=Pass`. Safety gate bypassable through the normal ORM path. Added
  `_reconcile_with_attempt` + regression check `evidence_cannot_contradict_attempt`.
- M1 major — `derive_state` took `list[-1]` on a `creation`-ordered query but compared with a
  `(recorded_at, creation)` key. Ordering now uses `creation` alone.
- M2 major — `recorded_at` was caller-suppliable and affected ordering. Same fix; `creation` is
  database-assigned and cannot be supplied.
- M5 major — race-prone upsert. Real unique index `unique_learner_competency` now enforced at the
  storage layer. NOTE: required bumping the DocType JSON `modified` timestamp to force a re-sync;
  `on_doctype_update` does not fire on an unchanged JSON.
- M7 major — two passes with no activity recorded yielded Mastered. Now requires two distinct
  activities.
- m5 minor — recompute flag cleared rather than restored, clobbering a nested context.
- m8 minor — `Refresh Due` absent from `STATE_ORDER` would raise on index lookup. Guarded.
- Harness `results` list accumulated across `run()` calls in one worker. Now cleared.

FOUND BY ORCHESTRATOR, NOT BY THE AUDIT
- **Controller class name mismatch.** `SparshSourceOfTruthRule` vs the `SparshSourceofTruthRule`
  Frappe derives via `doctype.replace(" ", "")`. The entire Source of Truth Rule controller —
  supersession included — was never loading. Codex reviewed that file as though it executed and
  reported on logic that was dead. This was the true cause of `rule_version_snapshot`.

DEFERRED (real, out of scope for the scaffold)
- B1 — `db.set_value` / `db_set` / raw SQL bypass `validate`. Inherent Frappe behaviour, present in
  every app on this bench. Mitigation is role restriction, audit logging and code review, not
  controller code. Document in the build brief.
- B3 — free-text fields can hold PHI; the verifier checks field names, not content. Operational
  control (de-identified scenarios), not field validation.
- M3 — force-delete bypasses `on_cancel` and can orphan Mastery. Blocking deletion would break the
  harness teardown; belongs with the permission model.
- M4 — existing certifications are not revoked when mastery later regresses. Needs the revocation
  workflow (see M14).
- M6 — Evidence competency need not match its Activity's competency. Partially closed (learner and
  activity now reconciled against the Attempt); full closure needs Activity→Competency validation.
- M8 — `human_review_status` and `mastery_contribution` are not consulted by derivation.
- M9/M10/M11 — the Attempt's `rule` Link is mutable, rule versions are editable after Attempts
  exist, and supersession fires on Draft. Rule immutability is its own slice.
- M12 — `retry_index` has no uniqueness or sequencing rule.
- M13 — `Sparsh Learner` / `Sparsh Reviewer` roles exist but hold no DocType permissions. Known gap,
  self-declared by Builder A. Needs its own slice.
- M14 — certification cannot be amended to Revoked because validation demands Demonstrated/Mastered
  for every status, including Revoked.
- M15, m1–m4, m6, m7, m9, m10 — logged, not actioned.

### Re-audit status
Code changed materially after the audit. The `sol` audit does NOT cover commit `9d1b3ff`. A
re-audit is required before this is treated as reviewed.

## 2026-09-12 · Permission model and re-audit
- Commit `9753305` permission model; commit `HEAD` re-audit fixes.
- Acceptance: `RESULT passed=14 failed=0`, twice consecutively, and on a clean install
  with both roles deleted first.
- Codex tier `gpt-5.6-sol` again (`astra` unavailable on this account).

### Re-audit dispositions
REJECTED WITH EVIDENCE
- Blocker "frappe.client.get_value bypasses row scoping". FALSE for Frappe v16 on this bench.
  `frappe/client.py:get_value` routes non-Single doctypes through `get_list`, which applies
  `permission_query_conditions`; `frappe.db.get_value` is used only for Single doctypes, and none
  of ours are Single. Verified by reading the installed source. A regression test now exercises
  `frappe.client.get_value` as an authenticated learner.

CONFIRMED AND FIXED
- #2 learners controlled evaluation fields on Attempt.
- #3 learners could forge escalation workflow state.
- #6 Evidence could credit an activity to the wrong competency.
- #8 equal `creation` timestamps read as "critical error answered". Tie-break on (creation, name).
- #9 reviewer/certifier attribution was caller-supplied.
- #10 the scope test would have passed even if the boundary were broken. Now authenticates as a
  learner and exercises get_list, frappe.client.get_value, cross-learner insert and Evidence create.
- #11 no test distinguished one activity from two. Now covered.
- M14 from the first audit: certification could not be revoked after regression.

DEFERRED
- #4/#5 System Manager retains delete on Mastery State and Attempt; no `on_trash` guard. Blocking
  deletion breaks the harness teardown and an administrator can reach the table regardless. Belongs
  with the retention policy, not the controller.
- #7 the unique index protects the invariant but the upsert has no retry, so a concurrent recompute
  raises a duplicate-key error rather than merging. Correct but not graceful.
- M8 `human_review_status` and `mastery_contribution` still not consulted by derivation.
- M9/M10/M11 rule immutability after attempts cite it.
- M12 `retry_index` sequencing.

### Coverage note
Both audits ran at `sol`. Neither covers the current HEAD: code changed after the re-audit. A third
audit would be needed to call HEAD reviewed.

## 2026-09-12 · Third audit + phases 3-6
Commits `5c900c1` runner, `973afb3` escalation, dashboards, `6b35159` domain proof,
`8bf3d70` orchestrator, `e26c2bc` safety clearance, HEAD audit-3 closure.
Acceptance moved 14 -> 24 checks, `failed=0`, twice consecutively at each step.

### Audit 3 (gpt-5.6-sol; astra still unavailable) — 2 blockers, 7 major, 5 minor
CONFIRMED AND FIXED
- BLOCKER: a later independent pass silently cleared a standing critical error. This was
  progression on aggregate performance — the one thing the gate exists to prevent. Clearance now
  requires a submitted, approved Human Review with `clears_critical_error`. Human Review became
  submittable because a clearance is a governance act.
- BLOCKER: Full certifications survived later critical errors. `recompute_mastery` now suspends an
  active certificate when the state regresses or a critical error stands, via an allow-on-submit
  field rather than by editing submitted history.
- MAJOR: activity-less Evidence could reach Demonstrated. An independent pass must now cite the
  activity it was earned on.
- MAJOR: attribution was rewritten on every save; moved to `before_submit`, fields marked no_copy.
- MAJOR: Mastery State was deletable; delete removed from every role.
- MAJOR: no identifier enforcement. Narrow guard added for the hospital MRN format and 12-digit runs.
- MINOR: the shell harness accepted `passed=0 failed=0`. It now asserts a minimum check count.

OBSOLETE
- MINOR tie-break on equal `creation`: there is no longer any ordering comparison in the gate.

ACCEPTED AS DESIGNED, NOT FIXED
- `is_restricted()` treats a privileged role as winning over Sparsh Learner. Inverting it would stop
  reviewers seeing their cohort and stop the runner grading. Documented rather than changed; revisit
  if a custom DocPerm ever grants create to an untrusted role.
- Learner field correction runs after Frappe's permission and link checks, so a learner must already
  send their own learner value. Safe, but it is the permission veto doing the work, not the
  controller.

DEFERRED
- No current-certification concept: parallel submitted Full and Revoked rows can coexist, and
  `derived_state` is a snapshot at last validation. Needs a certification ledger design.
- Evidence cancellation payloads that also change learner/competency leave the original pair stale.
- Activity.competency remains editable after Evidence cites it.
- Attempts remain deletable/discardable by System Manager.

### Coverage
No audit covers the current HEAD: three rounds of fixes landed after audit 3 ran.

## 2026-09-12 · Audit 4 + phases 7-8
Commits: refreshers, matrix loader, audit-4 blockers, audit-4 majors.
Acceptance 26 -> 35 checks, `failed=0`, twice at each step. 13 top-level doctypes, 8 child.

### Audit 4 (gpt-5.6-sol) — 4 blockers, 10 major, 9 minor
BLOCKERS, ALL CONFIRMED AND FIXED
- A learner could read the answer key. `Sparsh Learner` held full read on Activity including
  `expected_response`, the hint ladder and critical-error markers. Those three fields now sit behind
  permlevel 1, which only reviewers hold. Combined with the next item this was a complete defeat of
  the evidence model through the ordinary API.
- The runner trusted caller-supplied `hint_level`/`retry_index`. Assistance is a fact about the
  session, so the server counts it from the attempt record; `submit()` no longer accepts either.
- `critical_error_cleared` is allow-on-submit and nothing validated it: a reviewer could set the flag
  or create pre-cleared critical evidence. It now requires a submitted, approved review citing that
  evidence, re-checked on update-after-submit.
- Cancelling evidence made a safety error vanish, and cancelling the clearing review left the
  clearance standing. Both closed.

MAJOR, CONFIRMED AND FIXED
- The runner claimed "a reviewer has been notified" when nothing was notified. It now raises a real
  escalation.
- Substring critical matching flagged "do not stop the medicine" as unsafe. Word-boundary matching
  with a negation guard; detection stays advisory, which is why every critical result is escalated.
- Evidence could upgrade a failed or assisted attempt into a clean independent pass.
- Runner attempts never set `rule`, so every `rule_version` was 0.
- Human Review's learner/competency were caller-supplied; copied from the evidence now.
- The learner dashboard hid certification suspension.
- `open_queue` exposed every learner's escalation text; `raise_question` accepted another learner's
  attempt. Both gated.
- The runner was callable by any authenticated account; callers must now be enrolled.

ALREADY FIXED BEFORE THE AUDIT REPORTED THEM
- Findings 5 and 8 (dashboard and orchestrator leaking another learner's record) were closed while
  audit 4 was still running — found by writing its own brief, which is a point in favour of writing
  adversarial prompts even when a model will run them.

DEFERRED
- Human-review activities record a Not Evaluated attempt but have no completion path: a reviewer must
  create the evidence by hand. Needs a review-to-evidence flow.
- The identifier guard misses hyphenated Aadhaar, `WS-12345`, phone numbers, emails, names and dates.
  Deliberately narrow; broadening it needs the programme's own identifier inventory.
- No rate limiting on runner/escalation endpoints.
- Retry metadata still weakly constrained.

### Coverage
Audits 1-4 all ran at `sol`; `astra` is unavailable on this account. No audit covers HEAD.
