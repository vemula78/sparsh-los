# Sparsh Learning OS (`sparsh_los`)

A domain-agnostic competency-learning engine on Frappe v16 — **not a course LMS**.

It does not ask which course a learner should complete next. It asks what experience would produce
the next piece of credible evidence of competence, and it records what the learner actually did,
how much help they needed, and which version of the programme's rules they were judged against.

Module: `Sparsh LOS`. 13 top-level DocTypes, 8 child tables.

## What it enforces

These are not conventions. Each one is covered by a check in the acceptance harness.

- **Mastery cannot be asserted.** No role may create, write or delete a Mastery State — System
  Manager included. It is recomputed from submitted Evidence or it does not exist.
- **A safety error is cleared by a person, not by performing well afterwards.** A standing critical
  error blocks progression and certification until a submitted, approved Human Review clears that
  specific evidence. Cancelling the evidence or the review does not clear it.
- **Help received is part of the record.** A pass after a hint is practice evidence and reaches
  Practising; only an unaided pass reaches Demonstrated. Assistance is counted by the server from
  the attempt record, never taken from the caller.
- **The person being assessed cannot read the answer.** Expected responses, hints and critical-error
  markers sit at permlevel 1, which only reviewers hold.
- **Evidence cannot contradict the attempt it cites** — outcome, assistance, learner, activity and
  critical flag are all reconciled.
- **A certificate does not outlive its evidence.** Regression suspends a standing certification.
  One certification stands per learner and competency, and a revocation names what it withdraws.
- **A rule change brings people back.** Superseding a Source-of-Truth Rule schedules a refresher for
  everyone judged against the old version.
- **No identifiable data.** A guard refuses the hospital's MRN format and Aadhaar-length digit runs
  in learner free text. Scenarios are de-identified by policy.

## Modules

| Module | Role |
|---|---|
| `runner.py` | instruction → response → evaluate → hint → retry → evidence |
| `mastery.py` | Derives state from Evidence; the critical-error gate; certification reconciliation |
| `orchestrator.py` | What the learner meets next, and why: prerequisite, remediation, practice, consolidation |
| `review.py` | The queue of attempts nobody has judged, and the path from one to Evidence |
| `escalation.py` | Learner questions with packaged context; the four reviewer dispositions |
| `certification.py` | Readiness: ready, blocked, or insufficient — always with the reason |
| `refresher.py` | Refreshers by elapsed time or by a rule changing underneath the learner |
| `dashboard.py` | Learner view, supervisor view, competency heatmap |
| `permissions.py` | Row-level scoping for learners; the identifier guard |
| `seed.py` | Loads the Source-of-Truth Matrix as Draft rules |
| `verify.py` | The acceptance harness |

## AI

None at runtime. Every path above is deterministic, so an ordinary practice session costs nothing
and works when no AI service is reachable. `Sparsh Evidence.ai_feedback_summary` exists for a future
caller to fill; nothing in this app calls a model or the network.

## Install

```
SITE=erp.sssihms.org ./scripts/install_verify.sh
```

Streams the app into the `internal-backend-1` container, installs it, and runs the harness.
`SKIP_VERIFY=1` proves the install plumbing only. `MIN_CHECKS` guards against a harness that
silently runs nothing.

Note: `bench get-app` on a local path is broken on this bench (`AttributeError: 'App' object has no
attribute 'org'`, bench 5.31). The script falls back to `pip install -e` plus an `apps.txt` entry.

## Verify

```
bench --site erp.sssihms.org execute sparsh_los.verify.run
```

One `PASS`/`FAIL` line per check and a final `RESULT passed=N failed=M`. Re-runnable: it clears its
own fixtures first and deletes them after.

## Load the programme matrix

```
bench --site erp.sssihms.org execute sparsh_los.seed.load_matrix
```

Creates the 17 Source-of-Truth Matrix rows as **Draft**. Nothing arrives validated.
`sparsh_los.seed.matrix_status` reports what still awaits the programme owner — including which
safety-critical rules are unvalidated and which rules are cleared to become fixed logic.

## Uninstall

```
SITE=erp.sssihms.org ./scripts/uninstall.sh
```

## Status

The engine is built and installed on the demo site. What it does **not** have is validated clinical
content: every rule in the matrix is Draft, and no scoring rule should be locked until the programme
owner has approved it. See `PLAN.md` and `PLAN-REVIEW-LOG.md` for the decisions and the audit trail.
