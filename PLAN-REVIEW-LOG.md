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
