## Why

GitHub can start two CI workflow runs for the same pull-request head and cancel
the older run. The cancelled suite may leave a uniquely named matrix placeholder
check in failure even after the newer suite's `CI Required` job succeeds. The
Codex label synchronizer currently aggregates that stale check and classifies an
otherwise green current head as failed, so it neither labels a clean review nor
requests the missing current-head Codex review.

## What Changes

- Treat the workflow run containing the most recent `CI Required` check as the
  authoritative GitHub Actions CI suite for that head.
- Ignore non-required GitHub Actions checks from superseded workflow runs while
  preserving required check contexts and non-Actions status evidence.
- Keep failures from the authoritative CI workflow blocking.
- Add regression coverage for the cancelled matrix-placeholder shape observed
  on a real current-head pull request.

## Impact

Codex review labels and review requests follow the latest completed CI suite for
the exact head instead of remaining blocked by stale checks from a cancelled
duplicate run.
