# Split the release guards out of the CI matrix

## Why

`ci.yml` subscribed to `pull_request: edited` because the beta release guard
reads validation evidence out of the release PR body. With
`cancel-in-progress: true` on the per-ref concurrency group, every PR title or
body edit — agents finalizing a description after pushing, review bots
rewriting summaries — cancelled the in-flight matrix and re-queued ~30 jobs for
an unchanged head. On 2026-09-08 this happened on eight campaign PRs (nine
cancelled runs, see `EXEC-REPORT` §4.2 of the slop-removal campaign) and
starved the runner queue for hours. Moving CodeRabbit's summary out of the PR
body (`keep-coderabbit-summary-out-of-pr-body`) removed one editor; it did not
remove the trigger.

## What Changes

- Remove `edited` from `ci.yml`'s `pull_request` trigger types. Keep `opened`,
  `reopened`, `synchronize`, `ready_for_review`, `push` to `main`, and
  `merge_group`.
- Move the `Beta release guard` and `Stable release guard` jobs — the only jobs
  that read PR metadata — into a new lightweight `release-guards.yml` workflow
  with its own concurrency group. It subscribes to `edited` (plus the same
  events as before) so release-evidence edits are still revalidated within
  seconds.
- Drop `beta-release-guard` from the `CI Required` aggregate (cross-workflow
  `needs` is impossible). Check context names are unchanged.
- Add a unit test that pins the trigger split so `edited` cannot quietly return
  to `ci.yml`.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `github-automation`: the CI matrix workflow must not restart on PR metadata
  edits; PR-metadata-dependent jobs live in a separate workflow that does. The
  CodeRabbit summary requirement's "edited-event CI" wording is updated to point
  at the release guards workflow.

## Impact

- `.github/workflows/ci.yml`, new `.github/workflows/release-guards.yml`,
  `tests/unit/test_ci_workflow_required_checks.py`, `.github/CONTRIBUTING.md`.
- Branch ruleset: unchanged. None of the 19 required contexts is a release
  guard or `CI Required`; every required context still comes from `ci.yml` or
  `simplicity-budgets.yml`.
- Behavior change for release PRs: a body edit no longer re-runs the matrix,
  only the guards. `CI Required` no longer reflects the beta guard; the
  publish-time guard in `publish-beta-release.yml` remains the hard gate.
- No application, dependency, setting, or UI changes.
