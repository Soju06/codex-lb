## Why

GitHub can start two CI workflow runs for the same pull-request head and cancel
the older run. The cancelled suite may leave a uniquely named matrix placeholder
check in failure even after the newer suite's `CI Required` job succeeds. The
Codex label synchronizer currently aggregates that stale check and classifies an
otherwise green current head as failed, so it neither labels a clean review nor
requests the missing current-head Codex review.

## What Changes

- Identify the GitHub Actions CI workflow from the most recent `CI Required`
  check, then treat the newest same-head run of that workflow as the
  authoritative CI suite — keeping a newer run pending until its own
  `CI Required` completes.
- Ignore GitHub Actions checks (including stale required contexts) only from
  superseded runs of that workflow, while preserving non-Actions status
  evidence and checks from independent workflows.
- Keep failures from the authoritative CI run blocking.
- Treat a manually re-run older workflow `run_id` as newer evidence when its
  current attempt started after a later-created run.
- Add regression coverage for the cancelled matrix-placeholder shape observed
  on a real current-head pull request.

## Impact

Codex review labels and review requests follow the latest completed CI suite for
the exact head instead of remaining blocked by stale checks from a cancelled
duplicate run.
