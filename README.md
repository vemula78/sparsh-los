# Sparsh Learning OS (`sparsh_los`)

A domain-agnostic competency-learning engine built on Frappe v16 — not a course LMS. It tracks
learners, competencies, evidence of demonstrated skill, and mastery state derived deterministically
from that evidence, with versioned rules governing how mastery is gated.

Module: `Sparsh LOS`. DocType count: 12 top-level and 8 child tables (20 total).

## Install

```
SITE=erp.sssihms.org ./scripts/install_verify.sh
```

Streams the app into the `internal-backend-1` container, installs it on the site, and runs the
behavioural verification harness. Set `SKIP_VERIFY=1` to prove install plumbing only, without
running the harness (used before all DocTypes exist).

## Verify

The harness runs on-site as:

```
bench --site erp.sssihms.org execute sparsh_los.verify.run
```

It prints one `PASS`/`FAIL` line per check and a final `RESULT passed=N failed=M` line.

## Uninstall

```
SITE=erp.sssihms.org ./scripts/uninstall.sh
```

Removes the app from the site and bench. Safe to run when the app is already absent.
