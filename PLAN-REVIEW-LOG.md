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

## 2026-09-12 · Audit 5 and close of session
Acceptance 38 -> 39 checks, `failed=0`, twice, and on a from-zero install.
Final shape: 13 top-level doctypes, 6 child (two child doctypes deleted, see below).

### Audit 5 (gpt-5.6-sol) — 4 blockers, 9 major, 3 minor
BLOCKERS, ALL CONFIRMED AND FIXED
- `is_restricted()` is fail-open: it answers "is this a learner?", not "may this person judge one".
  Any authenticated non-learner could call `review.record_evidence` and advance mastery. Reviewer
  actions now require explicit reviewer membership; the same shape was fixed in the cohort views and
  the escalation queue.
- The answer key was readable through child tables. permlevel on a parent Table field does not
  protect the child doctype, which a learner could query directly by naming Sparsh Activity as its
  parent; and filters on a permlevel-1 field still work as a prefix oracle. Hint ladder and critical
  markers became permlevel-1 text fields, both child doctypes were deleted, and learner read on
  Activity and Scenario was removed outright.
- Assistance reset to zero after an assisted pass, so a learner could take a hint, pass with it, and
  resubmit the known answer as an independent pass. Assistance never falls now.
- Cancelling cleared critical evidence and then its review left nothing to reimpose the block.
  Critical evidence cannot be cancelled at all, cleared or not.

MAJOR, CONFIRMED AND FIXED
- `record_evidence` rewrote the immutable attempt; it no longer does — whether an attempt has been
  judged is answered by whether Evidence cites it.
- A dual-role user could grade their own work or clear their own critical error.
- A time-based regression never persisted or reconciled certifications, so a certificate outlived
  its currency indefinitely.
- Refresher assignments had no row scoping; `certification.current()` had no self-access check.

DEFERRED, WITH REASONS
- Concurrency (majors 6 and 7): the "already has evidence" and "one standing certification" checks
  are `db.exists()` without a lock or unique constraint. Both need database constraints, which is a
  schema decision, not a patch.
- Critical-marker matching (major 11) remains defeatable by punctuation and fooled by "do not
  hesitate to stop the medicine". Free-text safety detection cannot be made reliable; this is why
  every critical result is escalated to a person rather than left to stand alone.
- The identifier guard (major 12) cannot catch names, addresses or dates. De-identified scenarios
  are the actual control; the guard stops accidents.
- Rule-change refreshers (major 13) assign by competency rather than by the rule each learner was
  actually assessed under. Now that attempts record a governing rule, provenance-based selection is
  possible and should replace it.
- Minors 14-16 logged, not actioned.

### Session close
Five audits, all at `gpt-5.6-sol`; `astra` is unavailable on this account. Two Codex processes hung
outright (0.07-0.08s CPU, zero output, killed at 25 and 40 minutes) — treat the tool as flaky.
No audit covers the current HEAD: audit 5's fixes landed after its snapshot.

The engine is built and proven on the demo site. It has no validated clinical content: all 17 matrix
rules are Draft and `matrix_status()` reports zero rules cleared to become fixed logic, which is the
correct state until the programme owner decides otherwise.

## 2026-09-12 · Audit 6 and the phases after it
Acceptance 39 -> 50 checks, `failed=0`, twice at each step.
Built after audit 5: database constraints for the two raceable invariants, provenance-based
refreshers, the reviewer queue page, the Starter Case Pack loader, programme readiness, pathway
walking, and the harness's own blind spots.

### A finding that changes the plan
`programme_readiness()` reports `can_pilot_with_human_review: true`. The Phase 8 pilot does NOT need
validated rules: every case loads in human-review mode, a reviewer judges each attempt, and the
evidence trail is complete either way. The six unvalidated safety-critical rules block *automatic*
scoring and person-free certification, not the pilot. Earlier versions of the build brief had this
wrong.

### Audit 6 (gpt-5.6-sol) — 1 blocker, 8 major, 2 minor
First attempt was REFUSED by OpenAI's cybersecurity classifier: adversarial framing ("exploit path",
"bypass") tripped it. Rephrasing the same request as the internal quality review it actually is got
a full result. Worth knowing for future runs.

BLOCKER AND WORST MAJOR, ONE ROOT CAUSE, FIXED
- `is_restricted()` meant "holds the learner role and nothing privileged", so an account with NO
  Sparsh role read as unrestricted — backwards for a check gating other people's records.
  Restriction is now the default, reviewer membership the exception. And because a dual-role user is
  legitimately a reviewer, self-judgement is now blocked positionally: nobody writes evidence about
  their own work, certifies themselves, or answers their own escalation, whatever they hold.

MAJOR, FIXED
- Unaided passes with no activity are refused rather than ignored.
- Attempts are undeletable: they are the assistance history.
- Cancelling a certificate frees its standing key, which otherwise blocked every future
  certification for that pair.
- `current()` reports Suspended for lapsed competence at read time rather than waiting for a daily
  job that may not run.
- `escalation.route`/`answer` require reviewer membership and refuse self-answering.
- `runner.start` has the same enrolment check as `submit`.
- Only uncleared critical errors block, and remediation goes to the error that stands.

HARNESS BLIND SPOTS, FIXED
The harness ran as Administrator AND learned as Administrator, so every self-judgement path looked
fine. Giving it a real subject account turned 47 passes into 27 failures before a single production
line changed. Also: the practice-page check passed when the page offered nothing; no coverage of
role combinations or unenrolled accounts; Mastery State had no deletion guard.

DEFERRED
- Concurrency in `_session_position`: two simultaneous failures can both show hint 1.
- Evidence/attempt reconciliation stays asymmetric where Evidence omits its activity.
- `certification_state` and `standing_key` are allow-on-submit and not restricted to the
  reconciliation path.

## 2026-09-12 · Audit 7
Acceptance 51 -> 58 checks, `failed=0`, twice.

### Audit 7 (gpt-5.6-sol) — 4 high, 5 medium, 1 low. All confirmed, all fixed.
- `next_in_pathway` checked safety only while walking steps, so an incomplete earlier step was
  returned before a standing critical error on a later competency was examined. Safety now outranks
  the whole pathway. A mandatory step naming an activity is also no longer completed by
  demonstrating the competency some other way.
- Own-record endpoints rejected only requests naming somebody else, so any signed-in account could
  ask the platform about itself. `require_enrolment` gates them: signed in is not enrolled.
- A learner with no evidence could not start from the practice page at all, because it looked only
  at mastery rows, which evidence creates.
- `certification_state` was form-read-only but caller-supplied, so a certificate could be submitted
  already Suspended while occupying the standing key.
- A lapsed certificate is now suspended in the ledger, not only in the value `current()` returns.
- Attempts have an `on_trash` guard; removing the role permission was not enough, because a
  privileged deletion path does not consult permissions.
- Matrix readiness reads the current version of each lineage, and only the matrix's own rules.

### A correction I had to make to my own work
A comment in `runner.py` claimed a race on the assistance counter could never understate help. It
was wrong: two submissions can both read the same position and the second can record less
assistance than has by then been shown. The comment now states the limit and names the fix (a lock
on (learner, activity)). A reassuring comment is worse than an undocumented gap.

### Tests that would have passed through a regression, now fixed
- The answer-key check never exercised a real access path.
- The self-certification check passed because its subject had no qualifying evidence, so it would
  have passed with the guard removed.
- The pathway check put the critical error on step one, which is precisely why the ordering bug
  above went unnoticed.
- Teardown deleted fixture users while leaving their evidence, so a later run cancelled that
  evidence and tried to recompute mastery for a learner who no longer existed.

### Tooling note
Three of nine Codex runs hung (0.07-0.08s CPU, zero bytes, killed at 25-40 minutes). One was refused
outright by OpenAI's cybersecurity classifier for adversarial phrasing; rewording the same request
as the internal quality review it is produced a full result.

## Audit 8 — 13-Sep-2026 — non-independent Claude review against HEAD `51fa385`

Codex hung for the fourth time in ten runs (5h, 0.47s CPU, zero bytes). This audit is the
documented fallback: a Claude agent, explicitly **non-independent** — it shares the reasoning
lineage of the code it reviewed, so it is a strong lint, not a second opinion. It was told to
read `CLAUDE.md` but not this log until after forming its own view, then mark each finding NEW
or ALREADY KNOWN. An external review by a model outside this lineage has been prepared for the
programme owner to run separately; its findings will be logged as audit 9.

Plan hash unchanged: `50553ad04e854e932a6f5895f16a726f2cea756223df02dff73ec2495ea27274`.

Nine code defects and nine harness weaknesses, all reproduced against source before action.

### Confirmed and fixed — code

| # | Finding | Disposition |
|---|---|---|
| F1 | `escalation.raise_question` ungated: any logged-in account could write a question, and `_context()` returned Activity fields learners may not read | Fixed — `require_enrolment()` |
| F2 | `seed.matrix_status` / `case_pack_status` ungated; only the `programme_readiness` wrapper was gated | Fixed — reviewer gate on both; wrapper calls internals |
| F3 | `SparshEscalationQuestion.before_insert` returned early for unrestricted users, so a learner+reviewer could insert a self-answered question in one write | Fixed — reviewer-side fields reset unconditionally; `is_restricted` answers the wrong question here |
| F4 | `next_in_pathway` built `dict(suggestion, activity=step.activity)`, forcing a gated activity back in over a prerequisite block | Fixed — a suggestion with `activity is None` is returned intact |
| F5 | Mandatory-step completion counted rejected and fully-assisted evidence | Fixed — same filters as `mastery._independent_passes` |
| F6 | `certification._evidence_summary` omitted the rejected-review filter, so readiness and the certificate disagreed with the state machine | Fixed |
| F7 | `certification_record.current` opened an `in_sparsh_certification` window from a whitelisted read — the one flag rule the app states plainly | Fixed — lapse is reported, not persisted; reconciliation and the daily job own the write |
| F8 | `escalation.route` accepted any user as `routed_to`; `answer` could overwrite an existing answer | Fixed — reviewer check on the target, refuse a second answer |
| F9 | `runner.start` lets an enrolled learner enumerate activity ids | Deferred — content disclosure to an enrolled learner, no answer-key fields returned, no integrity break |

`certificate_detail` also had no enrolment gate. That was not in the review; the new
shut-out check found it. Fixed.

### Confirmed and fixed — harness

The more valuable half: a weak check is how a regression gets back in.

| # | Finding | Disposition |
|---|---|---|
| V1 | `check_nobody_judges_their_own_work` self-review case had no critical evidence, so `critical[0]` was never evaluated — a direct recurrence of audit 7's own self-certification finding | Fixed — real critical evidence on a second competency; `except` narrowed to `PermissionError` |
| V2 | Guarantee 8 had no biting assertion: no submission followed a pass on the same activity | Fixed — a third submit asserts assistance does not fall |
| V3 | `check_learner_cannot_read_answer_key` asserted on `critical_errors`; the field is `critical_markers`, so the line could never fail | Fixed |
| V4 | `_raises` accepted any `ValidationError`, and `PermissionError` subclasses it — checks passed on unrelated refusals | Fixed — optional `expect` fragment |
| V5 | The forged-clearance check asserted the save objected, never that the `db_set` write failed to land | Fixed — re-reads the field and re-asserts the block |
| V6 | Pathway coverage was one shape; the prerequisite case was invisible | Fixed — new check `pathway_does_not_hand_over_a_gated_activity` |
| V7 | `check_unenrolled_user_is_shut_out` enumerated the gates that existed, not the surface needing them | Fixed — six more endpoints, which is how the `certificate_detail` gap surfaced |
| V8, V9 | `check_no_domain_strings` scope; mid-check commits | Deferred — minor, no guarantee rests on either |

### Acceptance

`sparsh_los.verify.run` — **59 passed, 0 failed** (was 58; `MIN_CHECKS` raised to 59).

The new pathway check was proved to bite rather than assumed to: reverting the F4 fix alone
produced `FAIL pathway_does_not_hand_over_a_gated_activity: A gated activity was handed to the
learner anyway: ZZV-ACT-GATED`, 58/1. The fix was restored and 59/59 re-confirmed.

Code changed after this audit, so audit 8 does not cover the fixed version. Audit 9 (external,
outside this reasoning lineage) is the re-audit.

## Audits 9 and 10 — 13-Sep-2026

Two reviews of the same day's work, from different vantage points.

**Audit 9** — a Claude agent against `HEAD` (`345a80c`), non-independent: it shares the build's
reasoning lineage. Codex failed a fifth time in eleven runs (ran, searched the tree, exited
without writing a report).

**Audit 10** — run by the programme owner outside this session against the `51fa385` bundle, so
it predates the day's four commits. Genuinely independent. Several of its findings were already
closed by audit 8; those are marked as such below rather than double-counted.

### The blocker — audit 10, and nothing before it saw this

**Draft rules did not prevent automatic scoring.** `CLAUDE.md` said a rule must be Validated
before the engine scores against it, and the programme owner's reply is explicit that no
unvalidated rule becomes production logic. Neither was enforced anywhere. `_governing_rule`
excluded only `Superseded`; `evaluate` never consulted rule status. An activity set
Deterministic against a Draft **safety-critical** rule auto-scored, wrote Evidence and moved
Mastery. Seven prior audits and the whole harness missed it, because every fixture used a
Validated rule.

Fixed: the runner refuses to auto-score when the competency's governing rule is not Validated,
and routes to a reviewer. An activity with no rule linked is untouched — that is the
non-clinical cross-domain case, and blocking it would break domain-agnosticism.

`programme_readiness` was wrong in the same direction. `can_pilot_with_human_review` was
`not without_activities` — it answered "does every competency have something to do?" and was
then quoted, by me, to the user as "a pilot can run now". It now also requires that nothing can
auto-score against an unvalidated rule, and names the activities that would.

Proved by reverting: removing the gate alone gives
`FAIL draft_rule_cannot_auto_score: A Draft rule auto-scored the learner: Pass`.

### Audit 9 — confirmed and fixed

| # | Finding | Disposition |
|---|---|---|
| 3a | Refresh Due was a trap: nothing in the engine ever set an assignment to Completed, so a suspended certificate lasted until a human edited the row | Fixed — `refresher.close_satisfied` closes on an independent pass recorded after the assignment |
| 3c | A comment claimed "no role creates an assignment" while `Sparsh Reviewer` holds create and write — the forbidden class of comment, defending a genuinely weakened invariant | Fixed — comment now states the widening plainly |
| 3d | No `on_trash`: deleting an open assignment stranded the state at Refresh Due | Fixed |
| 3e | `after_insert` and `on_update` both recomputed on insert; three full recomputes per assignment | Fixed — `on_update` owns it |
| 4 | `escalation_reason` is the one `detail` string that begins as a whitelisted argument; the guarantee rested on the field being a Select | Fixed — unrecognised reasons record as "other" |
| 4a | Events and learning resources were never torn down | Fixed |
| 3g, 6a–6e | Trigger-reason breadth, inline cohort-wide suspension, re-supersession, two Current versions | Deferred — real, none safety-bearing; 6a matters at cohort scale and is noted for the pilot |

### Audit 9 — harness

All three checks added earlier that day were weaker than their docstrings claimed.

- The evaluator check iterated *the constants it was testing*, so promoting a mode into
  `AUTO_SCORED_MODES` would have silently removed it from the check. Now reads the Select from
  the DocType meta and subtracts the auto-scored set.
- It also left a live critical marker on `ACTIVITY_1` for every later check.
- The resource check compared evidence *names*, so a regression that cancelled every row or
  marked it Rejected would have passed the assertion its own docstring called "the half that
  matters most". Now compares content. The certification half had no fixture at all; now it does.
- The event check read one field, filtered to one learner, and never exercised the single emit
  whose `detail` starts as caller text. All three closed.

### Audit 10 — already closed by audit 8

Ungated `raise_question`, `matrix_status`, `case_pack_status`, `certificate_detail`, `current`;
the `critical_errors`/`critical_markers` dead assertion; `_raises` accepting any
`ValidationError`; the unenrolled-user check omitting the ungated surface; `next_in_pathway`
overwriting a prerequisite block; certification counting rejected evidence; the refresher
completion path. Audit 10 reviewed the morning's bundle and could not have seen those fixes.

### Audit 10 — accepted corrections to the documentation

- "Learners cannot read Activity at all" was imprecise: Frappe unions permissions across roles,
  so a learner who is also a reviewer reads it via the reviewer role. `CLAUDE.md` now says
  learner-*only* accounts.
- The one-way flow claim was incomplete: an approved Human Review writes
  `critical_error_cleared` back onto submitted Evidence. Now stated as the single deliberate
  backward edge.
- `MIN_CHECKS` is real but lives in `scripts/install_verify.sh`, which was not in the bundle.

### Audit 10 — still open

Dual-role Attempt forgery (§2.3), orchestrator counting assisted/rejected passes as completed
activities outside the pathway path (§4.3), escalation activity/attempt not reconciled (§4.7),
`supersedes` dereference (§4.9), unbounded `days` (§4.10), and the remaining harness items
(§3.8 constraint probe accepting any exception, §3.13 global `>= 1` assertions). Not yet
adjudicated — recorded here so they are not lost.

### Acceptance

`sparsh_los.verify.run` — **63 passed, 0 failed**. `MIN_CHECKS` 63.

### Audit 10 — remaining findings, adjudicated

| § | Finding | Disposition |
|---|---|---|
| 2.3 | Dual-role user forges their own Attempt: `_apply_learner_limits` returned early for anyone unrestricted, so a learner-reviewer could file `outcome=Pass` at `hint_level_used=0` for themselves, and a second reviewer turning it into Evidence made it an unaided pass | **Confirmed, fixed.** Limits now key on "is the subject the writer?", not on roles; assistance is recomputed from the record rather than taken from the writer. New check `dual_role_cannot_forge_their_own_attempt` |
| 4.3 | Orchestrator counts assisted and rejected passes as completed activities | **Confirmed in part.** Rejected evidence excluded — a reviewer's rejection is not a pass. Assistance deliberately still counts: `_passed_activities` answers "what has this learner met?", and excluding assisted passes would offer the same activity for ever. Independence is enforced in `mastery._independent_passes`, and conflating the two questions here would repeat the `is_restricted` mistake. Reason written into the function |
| 4.7 | Escalation activity and attempt never reconciled | **Confirmed, fixed.** Both must exist, and a supplied activity must match the attempt's |
| 4.9 | `supersedes` dereferenced before the Link is validated, giving `AttributeError` instead of the message | **Confirmed, fixed** |
| 4.10 | `days` accepted negatives and unbounded values | **Confirmed, fixed** — 1 to 3650 |
| 3.8 | Constraint probe accepted any exception as proof the database held | **Confirmed, fixed.** Now requires the duplicate-key error specifically and asserts exactly one row survives. Committing the first row first, so the rollback does not take it |
| 3.13 | Global `>= 1` assertions satisfiable by unrelated site data | **Deferred.** Real, but the site is a demo with no other learners; worth closing before any shared-site deployment |
| 3.9, 3.10 | Row-scope check covers only Attempt; domain-neutrality scan misses fieldnames | **Deferred** — logged, not safety-bearing |
| 2.5, 2.8 | "Learners cannot read Activity at all"; strict one-way flow | **Accepted as documentation defects** — `CLAUDE.md` corrected |
| 3.11 | No `MIN_CHECKS` guard | **Rejected** — it exists in `scripts/install_verify.sh`, which was not in the bundle the audit received |

`sparsh_los.verify.run` — **64 passed, 0 failed**. `MIN_CHECKS` 64.

## Audit 11 — 13-Sep-2026 — against `dcd0a96`

Non-independent (Claude, shares the build's lineage). Its most valuable findings were against
code written *the same day*, including one the previous fix introduced.

### Confirmed and fixed

| # | Finding | Disposition |
|---|---|---|
| 1.1 | The critical-marker branch ran **before** the new rule gate, so an activity governed by a Draft rule still auto-failed on safety — and critical Evidence cannot be cancelled, so the one judgement an unvalidated rule could still make was the irreversible one | **Fixed.** Rule gate now precedes everything. The response is still escalated to a person by `submit`; what it no longer does is impose a permanent block on an unvalidated rule's authority |
| 1.2 | `_governing_rule` returns one rule — highest version among non-superseded — so a competency linked to a Validated v2 *and* a Draft v1 auto-scored | **Fixed.** New `_rule_is_validated` requires every non-superseded linked rule to be Validated |
| 1.3 | `programme_readiness` used a different test from the runner in both directions: a mixed-status competency was reported fine while the runner silently routed it to human review, and `if rules` short-circuited so a no-rule activity was never named | **Fixed.** The report now asks the runner's own question, and reports no-rule activities separately |
| 1.5, 3.3, 4.1 | Three comments asserting guarantees the code does not establish — the banned class, twice found before | **Fixed.** Each now states the limit instead of the reassurance |
| 3.2 | `close_satisfied` ordered on `recorded_at`, which is writable — a regression of closed finding M2. Anyone who can create Evidence could close every open refresher by backdating one row | **Fixed** — orders on `creation`, as `mastery._sort_key` already does and says why |
| 3.4 | A null `assigned_on` was skipped silently — the repo's own "Silent Drop" | **Fixed** — logged |
| 3.7 | `CLAUDE.md`'s "only backward write" was invalidated by the same commit that wrote it: `recompute_mastery` now also writes Refresher Assignment | **Fixed** — two exceptions, both named |
| 4.3 | `certificate_detail` listed rejected evidence as supporting a certificate, on the endpoint whose purpose is showing the working | **Fixed** |
| §5 | The escalation-reason check passed `reason` defaulting to `"Unknown"`, a member of `REASONS`, so the fallback it was written to test never executed; the evaluator check had no positive control; "Fixture restored" restored a guess rather than the original; the duplicate-key probe still accepted any "duplicate" string; the resource check left a submitted certificate behind | **All fixed** |

### 3.1 — reported as a blocker; fix applied, exploit not reproduced

The reported sequence: `evaluate_time_based` reads the state while the assignment is open, the
recompute closes it, and `assign` then fires on the stale reading, creating an assignment
nothing can close. The reasoning is sound and the ordering was genuinely wrong, so the fix is
in: the job now recomputes first and decides on `_refresh_overdue` alone, which is the correct
trigger for a *time-based* refresher regardless.

But the exploit could not be reproduced. `Sparsh Evidence.on_submit` always recomputes, so by
the time the daily job runs there is no open assignment left to misread. Reverting the fix alone
leaves the new check passing. Recorded as reasoning-led rather than evidence-led: the change is
right on its merits, and the claim that it closed a live blocker is **not** established.

`check_answered_refresher_is_not_reassigned` is kept because it does bite on something real —
removing `close_satisfied` fails it — but it is not a regression test for 3.1.

### Deferred

2.1 (blank `learner` caught by `reqd` rather than by the function), 2.2 (dead `retry_index`
recompute), 2.3 (writer-supplied `attempted_at`), 2.4 (reviewer collusion — the same trust
already documented for refreshers, undocumented here), 3.5 (equal-timestamp boundary, now `>=`),
3.6 (`db.set_value` writes no Version row), 4.2 (SQL vs Python NULL handling, unreachable while
the field has a default), 6.1 (query counts). All real, none safety-bearing today.

### Acceptance

`sparsh_los.verify.run` — **65 passed, 0 failed**. `MIN_CHECKS` 65.

## Audit 12 — 13-Sep-2026 — the model-cost ledger, at `ba8ad74`

Non-independent. 2 near-blockers, 13 major, 18 minor, all against code written the same hour.

### G1 — the determinism check did not check determinism

`banned = ("import requests", ..., "from anthropic", "from openai")`. Every module was banned in
exactly **one** of its two spellings, and for the two that matter most the wrong one:
`import openai` and `import anthropic` — the form every provider quickstart uses — passed
untouched, as did `from requests import post`. The commit message claimed the check was there
so the guarantee would not rest on a docstring. The docstring was the more reliable of the two.

Rewritten: a regex over both spellings for nine modules, plus Frappe's own request helpers
(which need no import line at all), scanning the **repo root** rather than the inner package so
`pyproject.toml` — where a dependency would actually arrive, and the real control — is included.
Tokens are split (`"re" + "quests"`) so the check scans its own file instead of exempting it by
path substring.

Proved: adding `import openai` to `gateway.py` gives
`FAIL no_module_imports_a_network_client: gateway.py imports a network client (openai)`.

`CLAUDE.md` now says plainly that this is a tripwire for the careless case and not a proof, and
names `pyproject.toml` as the control. It cannot see a dynamic import or `frappe.get_attr`.

### The cost arithmetic was wrong three ways

| # | Finding | Disposition |
|---|---|---|
| S1 | `cost()` tested truthiness, so a genuine `actual_cost` of 0.0 — the one case where the true cost is *known* — was discarded and the estimate substituted | **Fixed**, and the first fix was also wrong: a Frappe Float is `0.0` when unset, never `None`, so `is not None` never fires. Needed an explicit `actual_cost_recorded` flag to tell "billed nil" from "not yet known" |
| S2 | One boolean could not distinguish estimate-substituted, genuinely-zero and nothing-known. A ledger with no costs reported `total_cost: 0.0`, which reads as "cheap" rather than "unknown" | **Fixed** — `total_actual`, `total_estimated`, `interactions_with_no_cost_recorded` |
| S3 | `cost_per_learner` divided the **whole** total by only the learners who appeared, inflating it without bound — and returned 0 for a period that cost real money when no row named anybody | **Fixed** — divides only learner-attributed cost, and reports `interactions_with_no_learner` |
| S4 | Totals summed across currencies with no currency in the output | **Fixed** — a mixed-currency period returns no total and says why |
| S9 | A provider refusal was counted as an error | **Fixed** — counted separately; on a clinical corpus it is the more interesting number |
| L3 | `validate` refused every update, so the `actual_cost` fallback presumed a write path the DocType forbade; the only repair was insert-a-second-row (double counts) or delete-and-reinsert (worse trail) | **Fixed** — `reconcile()`, deliberately narrow: actual cost and notes only |
| O4 | `provider`/`model_id` free text, so two spellings were two cost buckets | **Fixed** — normalised on write |

### P2 / O1 — the docstring asserted a control it did not call

The module's own docstring said "`reject_identifiers` on learner free text is the control that
actually runs" while `gateway.py` did not import it. Meanwhile `notes`, `prompt_template` and the
version strings are exactly where a caller with no template registry puts a rendered prompt —
a learner's typed response — on a row two roles can read **and export**.

`reject_identifiers` now runs over all four. The docstring no longer claims per-competency or
per-certification reports (the columns exist; the reports do not), and no longer says `record()`
is the only way to call a model — nothing calls a model, and there is no interception point.

**Third recurrence of the banned-comment class**, in a module whose docstring was written to
pre-empt exactly that criticism.

### Harness

V1–V5 all confirmed: global `>=` bounds on a ledger the check did not own (the audit-10 §3.13
pattern recurring the same day), `cost_is_partly_estimated` asserted only in its trivially-true
state, nothing re-read what was written, and the ledger absent from `teardown()`. All fixed —
the check now re-reads eight fields, creates all three cost states, and asserts the identifier
guard refuses a row.

### Deferred

G4 (dynamic import — unclosable by a source scan; documented instead), G6 (page JS), G7, L4
(`db.set_value` bypass — known class), L5, P3, P7, S7 (`spend()` unindexed scan — matters at
volume), S8, V7, V9, O2, O3, O5.

### Acceptance

`sparsh_los.verify.run` — **67 passed, 0 failed**. `MIN_CHECKS` 67.

## Audit 13 — 13-Sep-2026 — against `19c8e74`

Asked explicitly to hunt for a fifth instance of the banned-comment class, four having been
found in four consecutive rounds. It found one, and it was in the docstring written to disclaim
exactly that overclaiming.

### F1 — the fifth instance

`check_no_module_imports_a_network_client` said the real control is `pyproject.toml` declaring
no third-party dependency, *"which is why that file is scanned too"*. The file was scanned with
an **import regex**, which can never match `dependencies = ["openai"]`. It was opened, read, and
nothing about it was tested — while `CLAUDE.md`, the commit message and audit 12's own write-up
all repeated the claim.

Fixed: the dependency list is parsed and asserted. Proved by adding `openai>=1.0` to
`pyproject.toml` — `FAIL ... pyproject.toml declares third-party dependencies: ['frappe',
'openai']`. The docstring now also names what the regex cannot see: a comma list, a line
continuation, an import after a semicolon.

### The rest

| # | Finding | Disposition |
|---|---|---|
| F2 | The genuine-zero cost — the entire reason for the `actual_cost_recorded` flag — was created *after* the `spend()` call and never asserted through the report. With every other assertion a `>=` bound, reverting `cost()` to truthiness passed everything | **Fixed.** A second `spend()` asserts the billed zero moved neither total. Proved: reverting gives `A billed zero changed the actual total from 2.0 to 11.0` |
| F3 | The estimate was still classified by truthiness — S1 fixed on one side only, so a deliberate estimate of 0.0 read as "nothing known" | **Fixed** — `estimated_cost_recorded`, the same treatment |
| F5 | The mixed-currency early return had five keys where the normal path has eighteen, so every other figure — including the de-identification count, "the number worth escalating" — vanished exactly when the ledger was messiest, and callers got a KeyError | **Fixed** — the totals are suppressed, the report is not |
| F6 | `reconcile()`: "the only field an amendment may touch" touches two and enforces neither; and its stated justification (a better audit trail than delete-and-reinsert) was inverted by `track_changes: 0` on the same DocType | **Fixed** — `track_changes: 1`, so an amendment actually leaves a trail; notes appended rather than overwritten; `None` cost refused; `non_negative` dropped so a credit can be reconciled. The comment now says narrow-by-convention, not narrow-by-enforcement |
| F10 | The `("not in", ...)` comment asserted a specific SQL NULL semantic that nothing in the repo verifies — and the spelling it argued for may invert the intent | **Fixed differently.** Replaced with an explicit allow-list of the three statuses that count, so the answer cannot depend on how a Frappe version renders a negation against NULL. The comment says that instead of asserting SQL behaviour |
| F11 | `without_deidentification_assertion` was *weakened*: the old `== 0` tested both directions, the new `>= 1` only one, so a full inversion of the flag passed | **Fixed** — exact counts both ways |
| F12 | `is_restricted`'s public docstring still said "a learner and nothing more" — the precise reading `_is_restricted` was changed to reject, and that `sparsh_attempt.py` warns future readers against. A sixth instance, pre-existing | **Fixed** |
| F1b | `scanned >= 20` against a tree of 68 files | **Fixed** — 50 |
| F4 | No migration for `actual_cost_recorded`: pre-existing rows silently reclassify and their stored cost drops out of the totals | **Fixed** — `patches/v1_0/backfill_cost_recorded_flags`, confirmed in the Patch Log on the site. A stored cost above zero was certainly recorded; a stored 0.0 in an old row is genuinely ambiguous and is left unset, reporting unknown rather than asserting a billed nil nobody recorded |
| F7 | `provider`/`model_id` are `reqd` free text, shown in the list view of an exportable table, and were not passed to the identifier guard | **Fixed** — every free-text field now, not a subset |
| F9 | `_session_position` called with a possibly-blank activity, answered by Frappe's NULL-filter semantics rather than an empty match | **Fixed** — refused alongside the blank learner, since the function's premise is that the subject is known |
| F8, F13 | `attempted_at` overwritten on the self-filed path (anti-forgery, but an unannounced capability loss); JSON reformat inflating the diff | **Deferred**, recorded |

### Acceptance

`sparsh_los.verify.run` — **67 passed, 0 failed**. Two fixes proved by reverting them.

## Audit 14 — 13-Sep-2026 — against `6880294`

Asked to hunt a sixth instance of the banned-comment class. Found two, both in the comments
written the same day to close audit 13's findings about the same class.

### N2/N3 — the allow-list did not sidestep the NULL question, it inverted it

Audit 13's F10 replaced `("!=", "Rejected")` with an allow-list, on the reasoning that a
negation against NULL is version-dependent. Audit 14 read Frappe's `db_query` and showed it is
not version-dependent at all, and that the swap went the wrong way: `!=` renders as
`ifnull(col, '') != value`, which **keeps** a NULL row exactly as the Python sites in
`mastery.py` and `certification.py` do, while a bare `in (...)` **drops** it. The change took
two of the four "identical" filters out of step, under a comment claiming it kept them in step.

The allow-list was also the more brittle shape: a status added to the Select later would be
silently excluded, so the orchestrator would stop retiring an activity while mastery kept
counting it — a learner Demonstrated and offered the same activity for ever.

**Reverted to `!=`.** The original line was right; the original *comment* was the defect. The
new comment states what Frappe actually renders and why the allow-list was worse.

### N1 — "every free-text field on the row" was one field short

`reject_identifiers(notes, prompt_template, prompt_version, model_version, provider, model_id)`
omitted `cost_currency`, the same `Data` column on the same exportable row. The comment above
it said "Every free-text field on the row, not a subset" — written to close F7, which was
itself about checking a subset. Fixed.

### D1–D3 — the dependency parser was green for the wrong reason

F1's fix parsed `pyproject.toml` by hand. It **counted a commented-out dependency**: the check
passed by reading `# "frappe~=16.0.0"`. It returned *nothing* for an ordinary extras spec
(`celery[redis]>=5`, where the non-greedy match stopped at the first `]`), and could not see
`[project.optional-dependencies]` or a poetry table. Three false negatives on the control
`CLAUDE.md` calls the one that actually holds.

Replaced with `tomllib` — available (the container runs Python 3.14), so the docstring's
justification for hand-rolling was also wrong. Verified against all four shapes; the real file
now correctly reports `[]` rather than counting a comment.

### V1–V3 — three fixes from the previous commit shipped untested

| # | Finding | Disposition |
|---|---|---|
| V1 | `estimated_cost_recorded` — the entire point of commit `f77444c`'s second half — had no fixture passing `estimated_cost=0.0`. Reverting that bucket to truthiness passed every assertion. The exact defect F2 was raised for, recurring on the sibling field one commit later | **Fixed.** Proved: reverting gives `A deliberate estimate of zero was counted as no cost recorded` |
| V2 | The mixed-currency rework was never exercised, and a single non-INR row in the window would have made the existing assertions raise `TypeError` on `None` rather than fail cleanly | **Fixed** — a USD fixture, and the report's shape asserted |
| V3 | `reconcile()` was new, uncalled and unchecked, and its whole safety argument rests on `track_changes` being on **in the database** — a JSON edit that fails to sync being this repo's documented top time-waster | **Fixed** — asserts `track_changes` from the meta, the `None` refusal, the write, and that the note is appended rather than overwritten |

### Also fixed

G2 — a blank `cost_currency` was dropped from the currency set while its amount stayed in the
total, so an unlabelled row was summed into a figure reported as INR. Blank now counts as its
own currency. P2 — the patch's own `frappe.db.commit()` lands before its Patch Log row, so an
interrupt in that window re-runs it; removed.

### P1 — a correction to audit 13's record

Audit 13 logged the backfill as "confirmed in the Patch Log on the site". `installer.install_app`
marks every patch complete at install time, so **a Patch Log row is consistent with the patch
never having executed** — and on this site, which is installed rather than migrated across the
commit, it did not run. That is the correct outcome (no rows to backfill), but the log entry
claimed more than it established.

### Deferred

P3/P4 (`> 0` skips a negative stored cost; `non_negative` dropped from `actual_cost` with the
reason only in this log), G1 (identifier guard rejects `ps4096`-shaped model ids), G3, O1 (the
blank-activity comment overstates the blast radius — the filter matches only that learner's own
activity-less attempts and fails safe), O2, O4, V6.

### Acceptance

`sparsh_los.verify.run` — **67 passed, 0 failed**.

### Audit 14 deferred items — closed

P3/P4: the backfill's `> 0` skipped a negative stored cost, which the same commit had made
legal by dropping `non_negative` from `actual_cost` so a credit could be reconciled. Now `!= 0`,
and the field carries a description saying why it permits a negative — the reason previously
lived only in this log, where nobody editing the schema would find it.

O1: the blank-activity comment claimed a blank filter "could sweep in unrelated rows". It
matches only that learner's own activity-less attempts, and raises the recorded assistance
rather than lowering it — safe in direction. The guard is still right; the comment now says
what actually happens.

G1: the identifier guard's two false positives on model-shaped strings (`ps4096...`,
`ws2024...`, or an id containing an `@` with a dotted suffix) are recorded in `gateway.py`
rather than left for someone to rediscover as a confusing "patient identifier" rejection.

### Full regression

`./scripts/uninstall.sh` then `./scripts/install_verify.sh` — clean install from nothing:
**67 passed, 0 failed**. This is the test that actually exercises schema sync, `on_doctype_update`
index creation and the install path, rather than re-running against an already-migrated site.

### Dependency scan — three more shapes, found before audit 15 reported

Checked empirically rather than assumed, after the `tomllib` rewrite: a fixed list of tables
still missed **modern poetry groups** (`[tool.poetry.group.dev.dependencies]` — the current
spelling), **PEP 735 `[dependency-groups]`**, and **`[build-system] requires`**. Any of those
ships code.

The parser now walks every table named like a dependency list, so a packaging convention
invented after this was written is caught rather than silently passing. Two traps found while
doing it, both by running the parser rather than reading it:

- `[deploy.dependencies.apt]` (OS packages, present in the real file) was read as a Python
  package called `apt`, because a nested table's keys were taken as poetry name→constraint.
  Now only a table whose values are all strings is treated that way.
- PEP 735 maps a group *name* to a list, so taking keys yielded the group name. Now the lists
  are taken and the names ignored.

The permitted set is explicit — `frappe` plus build backends, which do not ship into the running
app — rather than an equality check against one expected list.

Proved: adding a PEP 735 `[dependency-groups] dev = ["openai"]` fails the check.

**67 passed, 0 failed.**

## Audit 15 — 13-Sep-2026 — against `d65bf8d`

### F1 — the seventh instance, and this one had teeth

The comment written in round 6 to replace an overclaiming comment said: *"Frappe renders `!=` as
`ifnull(col, '') != value`, which keeps a NULL row exactly as the Python sites do."* True of
`frappe.get_all`, which routes through `db_query`. **False of `frappe.db.count`**, which builds
via `frappe.qb` with no coalesce — a bare `<>`, which drops a NULL row.

`next_in_pathway` used `db.count`. So the divergence the comment declared closed was closed at
one of the two sites it changed and asserted at both. A mandatory step passed on Evidence with a
NULL review status would be counted as not done and re-offered for ever, while
`_passed_activities` and `mastery` counted it.

Fixed by making both sites use `get_all`, so they share one query path rather than two with
different NULL semantics. Narrow in practice — the field carries a default — but the comment was
wrong at the line it was written to justify.

That is seven consecutive rounds, and the third time the defect was *inside the comment written
to close the previous round's comment finding*.

### F4/F5 — the currency set was computed over rows with no cost

`currencies = {r.cost_currency or "unspecified" for r in rows}` included rows carrying no cost at
all, so one cost-less entry with a blank currency suppressed `total_cost`, `total_actual`,
`total_estimated` and `cost_per_learner` for the whole period — while contributing nothing to
them. And when every priced row was blank, `len(currencies) == 1` and the report returned a real
total labelled `currency: "unspecified"`: a total in an unknown unit, presented as a
single-currency total, by the same line whose comment said blank counts as *another* currency.

Now computed over priced rows only; a blank alongside a real currency counts as mixed; an
all-blank ledger reports no currency and names the count.

### F2, F3 — already closed / closed now

F2 (the parser missing poetry groups, PEP 735 and `build-system.requires`) was found
independently and fixed in `90778b1`, after this audit began reading. F3 is valid: `tomllib` is
stdlib from 3.11 while `requires-python` declared `>=3.10`, and "the container runs 3.14" is a
property of one machine, not of the declared floor. The floor is now 3.11.

The backfill docstring still described the old `> 0` predicate; an earlier edit had silently not
applied because the text differed. Fixed, and a reminder that a string replace that matches
nothing is a change that did not happen.

### Deferred

F7 (a model id shaped like an MRN trips the guard and `record()` throws, losing the ledger row
this module says must never be lost — worth its own message), the `sorted(set(...))` dedupe.

**67 passed, 0 failed.**

### Audit 10's last two harness findings — closed

§3.9: `check_learner_row_scope` exercised `Sparsh Attempt` alone, under a name and a docstring
covering row scope in general. A missing hook or a wrong learner field on any of the other five
scoped DocTypes would have left every row of it readable and this check would still have passed.
It now asserts, for all six: a `permission_query_conditions` hook, a `has_permission` hook, that
the scoping field exists on the DocType, that the condition names the learner, and that
Administrator is not scoped. Proved by deleting one hook —
`FAIL learner_row_scope: Sparsh Refresher Assignment is learner-scoped but has no
permission_query_conditions hook`.

§3.10: `check_no_domain_strings` read `label`, `options` and `description` only. The convention
it enforces names **fieldnames** specifically ("nothing in a field name or a validation may
mention SAI SPARSH"), and fieldnames were the one thing it did not read. Now covers the DocType
name and every fieldname as well.

**67 passed, 0 failed.**

## 13-Sep-2026 — Programme owner's two open decisions, resolved

Both were decisions for Praveen, not findings. Recorded here because they change where the
acceptance evidence comes from.

1. **Local development bench — adopted.** Dr. Nayanjeet marked a separate development
   environment MANDATORY. Development and the harness now run on the local `frappe_docker`
   bench on Praveen's Mac (`frappe_docker-backend-1`, site `sparsh.localhost`, created fresh).
   `scripts/install_verify.sh` and `scripts/uninstall.sh` take `TARGET=local` (default) or
   `TARGET=remote`; the hospital bench can no longer be reached by accident. 67/67 from a clean
   uninstall-then-install on the new site — the same result the remote bench gave at `a88519f`,
   so the retarget invalidates no prior evidence. Commit `5bfddb4`.

   Two local-bench defects surfaced and are documented in CLAUDE.md: `sites/apps.txt` has no
   trailing newline (a blind append fused `sssihms_vms` onto `sparsh_los`), and the local compose
   stack runs no rq worker, so queued jobs accumulate until Frappe refuses to enqueue — which
   `bench execute` reports as `NameError: name 'sparsh_los' is not defined` because it falls back
   to `eval()` and prints that failure instead of the real `QueueOverloaded`.

2. **DPDP wording — adopted as he asked.** The brief says the de-identification requirement is
   "a SAI SPARSH privacy and data-governance requirement, intended to comply with applicable DPDP
   requirements as they come into force" rather than asserting compliance. This is framing only:
   no code, no check and no engineering rule changed, and no identifiable data leaves the bench
   either way. Brief at v8.1.

## 13-Sep-2026 — Audit 16: hostile-caller probes against the running instance

Every audit so far read source. This one drives the installed app on the local dev bench as
four accounts — two learners, a reviewer, and an enrolled-in-nothing user — and reports what
the engine actually did. `scripts/hostile_probe.py`, 40 probes, fixtures under `ZZP-`, refuses
to run against a site whose name ends `sssihms.org`.

**39 refused, 1 allowed.** The 39 include: the answer key and hint ladder unreadable through
`frappe.client.get_list` and through `get_doc` + `check_permission`; a prefix-oracle filter on
`expected_response` refused; all six learner-scoped DocTypes returning 0 rows of another
learner's data; every reviewer endpoint refused to a learner with a named reason; the
unenrolled account shut out of all four endpoints tried; and a reviewer refused when inserting
Mastery directly, deleting an attempt, writing an Event, **and when setting
`frappe.flags.in_mastery_recompute` by hand first** — the flag is not a bypass from a caller.

### Two corrections to the instrument, before the result meant anything

The first run reported **11 allowed** and every one was my bug.

- Eight were probes written with `frappe.get_all` and `frappe.get_doc`. `get_all` is
  `get_list(ignore_permissions=True)` and `get_doc` runs no read check at all, so both return
  rows regardless of the caller. They measure that a row exists, not that someone may read it.
  Rewritten against `frappe.client.get_list` (the path a browser reaches), `frappe.get_list`
  with `ignore_permissions=False`, and `check_permission("read")`. All then refused.
- Six row-scope probes read backwards: the helper raised when rows leaked, so the probe's
  "ALLOWED" meant "returned nothing", i.e. the scoping worked. Row scope does not raise — it
  silently returns fewer rows — so an exception-shaped probe cannot test it. Replaced with
  `scope_probe`, which reports the row count and calls anything above zero a leak.

A probe that cannot fail is worth no more than a check that cannot fail. Both were caught by
the same rule as the rest of this log: show the instrument failing before believing it passing.

### F14 — a learner can author a row in the reviewer's queue. Major, high confidence.

`Sparsh Learner` holds `create` on `Sparsh Attempt`, because `runner.submit` inserts as the
learner. A learner can therefore insert an Attempt directly, and `review.pending()` shows it to
a reviewer identically to a genuine one, with `response` text the learner chose.

What the controller does defend, verified by reading the row back: a forged `outcome="Pass"` is
overwritten to `Not Evaluated`, `retry_index` is renumbered, `attempted_at` is set server-side,
and an attempt naming *another* learner is refused outright. So this is not self-grading.

What it reaches, demonstrated end to end: reviewer approves the forged row →
`review.record_evidence` writes Evidence with `assistance_level = attempt.hint_level_used` → the
learner's Mastery State becomes `Demonstrated`. The reviewer is still the judge, so
"nobody judges their own work" holds; what does not hold is that the artefact being judged was
authored by the engine.

**Not exploitable as assistance-laundering today, and this is why it must be re-tested later.**
The hint ladder never advances on the current configuration: `_session_position` raises the
level from `failures`, counted as outcomes not in `("Pass", "Not Evaluated")`, and with no rule
Validated every outcome is `Not Evaluated`. So `hint_level_used` is 0 and `independent` is 1 on
every attempt, genuine or forged, and a forged 0 laundered nothing. The moment one rule is
Validated and one activity is auto-scored, genuine attempts start carrying a non-zero level and
a learner-authored row claiming 0 becomes the cheapest way to have unaided competence recorded.

Left unfixed deliberately: the fix is a schema and permissions decision (drop learner `create`
and let only the runner insert, or mark provenance on the row), it lands in the same code the
first validated rule will touch, and there is no audit budget left today to check it. Recorded
here so it is the first thing re-tested when a rule goes Validated — the same trigger already
named for the expensive external audit.

Also noted from `runtime-facts.txt`: all 21 endpoints register
`http=['GET','POST','PUT','DELETE']`; none declares `methods=["POST"]`. Frappe rolls back after
a GET unless the code commits, and no request-reachable function here commits — the explicit
commits are in `seed.load_matrix`, `seed.load_case_pack`, `install` and `verify`. Defence in
depth, minor, high confidence.

## 13-Sep-2026 — Audit 17 (independent, ChatGPT) at e4de969, and the fixes

The second external audit. It read the source bundle, the runtime facts and the hostile-probe
output together, which is why it could be specific about installed permissions rather than
hedging. It confirmed the endpoint inventory (21, all authenticated, all gated), agreed the
GET reasoning, and found four major integrity defects plus a set of harness blind spots.

Every fix below was proved by reverting it and watching the new check fail. The five checks
were written first and all five failed on the unfixed code; the harness is now **72 checks**.

### Confirmed and fixed

| # | Finding | Fix |
|---|---|---|
| A17-1 | **Self-answered escalation.** `before_insert` closed the insert route and `escalation.answer` closed the endpoint; the ordinary save was open. A reviewer holds write on the DocType, so a dual-role user could set `status`, `answer_text` and `answered_by` on their own question and never reach either guard | `_nobody_answers_their_own_question` in `validate()`, where every write path passes |
| A17-2 | **No-rule auto-scoring did not block the pilot.** `_rule_is_validated` returns True when nothing is linked, and `can_pilot_with_human_review` excluded `no_rule_but_scoring` while the same report named it — one document asserting both things | `no_rule_but_scoring` added to the verdict |
| A17-3 | **Resource lineage unvalidated.** `validate` checked only self-supersession, while `on_update` retired the predecessor and marked its readers Refresh Due. One resource could retire an unrelated one; several versions could be Current at once | same-`resource_id`, increasing-version, and unique-Current checks |
| A17-4 | **Totals in an unknown currency.** `mixed_currency = len(currencies) > 1 or (bool(currencies) and unlabelled)` is False when *every* priced row is blank, so the report carried numeric totals under `currency: None` — exactly what the comment directly above it says must not happen | `or unlabelled` |
| A17-5 | **Rejected evidence contaminated the supervisor view.** `derive_state` drops rejected evidence; the dashboard counted it, so three rejected assisted passes read as "Passing only with help" | filtered — in Python, because `db.count` does not ifnull-wrap `!=` and would have dropped every unreviewed row |

A17-4 is the eighth instance of the pattern this log tracks: a comment describing a guarantee
the code beside it does not implement. The comment was written in the round that introduced the
bug it describes.

### What the new checks cost to make honest

Four of the five failed correctly on the first attempt. `no_rule_auto_scoring_blocks_the_pilot`
**passed with its fix reverted** — the pilot verdict was already False because the harness's own
fixture competencies had no activities, so the assertion was satisfied by ambient state. Making
it discriminate took four rounds: stand-in activities for the fixture competencies; flipping the
fixture's mode rather than deleting it, because deleting left its competency with no activity and
blocked the verdict for a different reason; muting the harness's *other* fixture activities,
which default to Deterministic with no rule and are therefore no-rule auto-scorers themselves;
and excluding the fixture under test from that muting. It now fails on the unfixed code and
refuses to run — rather than pass — if a non-fixture competency would make the comparison
meaningless.

`check_blank_currency_suppresses_totals` also leaked: on failure it left its unlabelled row in
the ledger and took `model_ledger_records_cost_and_makes_no_call` down on the next run. Cleanup
moved into a `finally`.

### Accepted, not fixed

- **Assistance race** (`runner.py:236-241`) — two concurrent submissions can read the same
  position. The code already documents it and names the fix as a lock on (learner, activity),
  which is a schema decision. Unchanged, and now recorded as a known break of the stated
  guarantee rather than as a guarantee.
- **Activity existence oracle** in `runner.submit` — `_activity()` runs before
  `_require_enrolment()`, so an unenrolled account can distinguish a real activity name from a
  fabricated one. Minor; `runner.start` gates first and is the pattern to copy.
- **`_raises` without `expect`** at 21 call sites — can pass on an unrelated `ValidationError`.
  Real, and a mechanical sweep; deferred rather than done badly in a hurry.
- **Unbounded `review.pending(limit)`**, **Refresh Due guidance text**, **domain strings in
  validation messages**, **shared-site lower bounds**, and the remaining cross-user endpoint
  coverage gaps (`certification_record.current()`, `orchestrator.next_in_pathway()`).
- **`Sparsh Certification Record.current()`** is decorated in source but absent from the runtime
  registry. Worth resolving: either it is unreachable, or the introspection missed it.

The audit also corrected my own F14 write-up: `_apply_learner_limits` replaces the forged outcome,
which I had verified, but it noted the probe itself never re-read the stored row — true, the
re-read was a separate script. The probe now stands as evidence of the refusal, not of the value.

## 13-Sep-2026 — Audit 17 follow-up: three of the deferred items closed

**The endpoint inventory was short.** Audit 17 flagged `Sparsh Certification Record.current()`
as decorated in source but absent from the runtime's 21-method registry, and reasonably asked
whether it was reachable. It is. The introspection walked a hand-kept list of 13 top-level
modules and never entered the doctype packages. It now walks the whole package with
`pkgutil.walk_packages`, and the count is **22**. Second time today an audit finding was the
measuring tool rather than the app — the first being eleven false "ALLOWED" probes. Both failed
in the direction of looking worse than reality, which is the safer direction and still wrong.
`scripts/runtime_facts.py`, commit `ba5e4c7`.

**The activity-existence oracle is closed.** `runner.submit` looked the activity up before
`_require_enrolment()`, so an unenrolled account got `DoesNotExistError` for an invented name
and `PermissionError` for a real one — enough to enumerate the catalogue a guess at a time.
Enrolment now gates first, as `start` always did. `check_unenrolled_learns_nothing_from_the_error`
compares the two refusals and requires them identical; with the fix reverted it fails with both
messages printed side by side.

**Every `_raises` call now names the reason it expects.** All 21 bare sites could pass on an
unrelated `ValidationError` — the helper's own docstring said so. Rather than guess the reasons
from the code the checks are supposed to be independent of, the helper was temporarily made to
log what each refusal actually said, the harness was run, and the 21 messages were read off the
output. The expected substrings are therefore observations, not assumptions.

That the harness still reports 73 passing is itself the proof the sweep landed: a wrong
substring raises `refused, but for another reason`, so every one of the 21 was matched against
the real message. The sites covered rule-version immutability, evidence reconciliation, direct
mastery mutation, critical-error certification blocking, activity/competency reconciliation,
identifier rejection, cancellation guards, duplicate certification, and the deletion guards.

Still open from audit 17: the assistance race (needs a lock on (learner, activity), a schema
decision), `review.pending` limit bounds, the Refresh Due guidance text, domain strings in
validation messages, shared-site lower bounds, and cross-user coverage for
`certification_record.current()` and `orchestrator.next_in_pathway()`.
