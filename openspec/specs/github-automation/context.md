# Context: github-automation

Normative requirements live in [`spec.md`](./spec.md). This document currently
covers the Simplicity budgets check and the Release guards workflow; the
codex-review label-sync machinery is summarized in the spec's Purpose.

## Simplicity budgets check

### Purpose

Make the simplicity effort self-enforcing. The `contribution-simplicity`
principles and the docs-site diet (`user-documentation`) shrink the entry-point
documents; the budgets check is the mechanical gate that keeps them shrunk.
Budgets live in data (`.github/simplicity-budgets.toml`) so every increase is a
one-line, reviewable diff rather than an argument.

### Decisions

- **Separate workflow, never ci.yml.** The workflow triggers on
  `labeled`/`unlabeled` so a just-applied override label re-evaluates the check
  immediately. Adding those types to ci.yml would re-run the entire sharded CI
  matrix on every `🤖 codex: ok` label sync from `codex-review-labels.yml`
  (15-minute cron + `workflow_run`). The standalone budget job costs seconds.
- **Labels are fetched live from the API, not the event payload.** Fork PR
  payloads and re-runs of old runs can carry a stale or empty label set; the
  workflow queries `/issues/<n>/labels` at run time (permissions:
  `pull-requests: read`) and passes the result to the script via `PR_LABELS`.
- **No `paths:` filter.** The job is cheap, and a required check behind a
  workflow-level paths filter would leave non-matching PRs pending forever
  (ci.yml solves this with the dorny-filter placeholder pattern — overkill
  here).
- **Stdlib-only script, plain `python3`.** Matches the
  `scripts/guard_beta_release.py` convention: runs before any dependency
  install; `tomllib` is stdlib on the runner's Python 3.12.
- **All-contributors block excluded from README counts.** The generated table
  between the `ALL-CONTRIBUTORS-LIST:START/END` markers is bot-managed, not
  hand-written complexity; counting it would make the line budget
  arithmetically impossible.
- **Exit 2 on missing nav target.** The nav budget reads `CORE_NAV_ITEMS` from
  `app-header.tsx`; the checker refuses to pass silently when the configured
  array vanishes, forcing any nav refactor to repoint `[core_nav]` in its own
  diff. Intentional coupling, not an accident. The same fail-loud exit 2
  covers a missing or malformed `.github/simplicity-budgets.toml` and an
  unclosed `ALL-CONTRIBUTORS-LIST` block (whose tail would otherwise be
  silently excluded from the count).

### Label caveats (operational)

- **Re-run after labeling works.** Because the label set is fetched live at run
  time, adding `simplicity-budget-approved` and then re-running the failed run
  picks it up; applying/removing the label also fires a fresh
  `labeled`/`unlabeled` run on its own. The failure message says exactly this.
- **merge_group and push carry no labels.** The override is a review-time
  acknowledgment only. If a PR would leave `main` over budget, the label cannot
  save the merge queue or the post-merge push run: the budget number in
  `.github/simplicity-budgets.toml` must be raised in the same diff.
  Alternative considered and rejected: skipping the check on `merge_group`
  would launder an over-budget `main` into green required checks.
- **Enforcement chain when merges bypass the queue.** With a plain required
  `pull_request` check, a maintainer-labeled over-budget PR can merge without
  the TOML bump; the very next push run on `main` then goes red, which is the
  intended alarm, not a gap: the label is restricted to maintainers, and the
  documented policy is that they either bump the TOML in the same diff or fix
  the exceedance immediately after. Hard pre-merge enforcement of the
  main-never-over-budget invariant requires routing merges through the merge
  queue (the `merge_group` run carries no labels by construction).
- **Label creation is out of band**:
  `gh label create simplicity-budget-approved` once, by a maintainer. Applying
  it is a deliberate approval act; no automation assigns it.

### Rollout

- Add `Simplicity budgets` to the required-checks ruleset only after one green
  run on `main` (GitHub cannot require a context that has never reported). It
  does not join ci.yml's `ci-required` aggregate — cross-workflow `needs` is
  impossible and the label triggers must stay out of ci.yml.

### Non-goals / deferred

- `README.zh-CN.md` is unbudgeted (banner-only treatment); a `[readme_zh]`
  section is a two-line follow-up if needed.
- A settings-count budget for `app/core/config` would need the app import
  graph (not stdlib-only) — deferred to the simplicity backlog.
- Docs-site pages are intentionally unbudgeted: depth is supposed to move
  there.

## Release guards workflow

### Purpose

`release-guards.yml` runs `scripts/guard_beta_release.py --mode pr` and
`scripts/guard_stable_release.py` for pull requests. The beta guard reads the
release PR body (checked validation checklist + exact head SHA), so it must
re-run when the body is edited. Those two jobs used to live in `ci.yml`, which
therefore had to subscribe to `pull_request: edited`.

### Decisions

- **Separate workflow, never ci.yml — same reasoning as Simplicity budgets.**
  `ci.yml` uses a per-ref concurrency group with `cancel-in-progress: true`, so
  every PR title/body edit cancelled the in-flight matrix and re-queued ~30
  jobs for an unchanged head. On 2026-09-08 eight campaign PRs each lost at
  least one run this way (runs 34216592285, 34216916484, 34217216971,
  34218615301, 34223748971, 34223782953, 34225099491, 34226177365,
  34226719090 all show `cancelled` for the head that later went green) and the
  runner queue was starved for hours. Agents finalizing descriptions after a
  push and review bots rewriting summaries both PATCH the body. The guards are
  stdlib-only and finish in seconds, so re-running them per edit is free.
- **Remove `edited` from ci.yml instead of skipping jobs on it.** A
  `github.event.action != 'edited'` condition would still create a new run in
  which every heavy job reports `skipped`; branch protection reads the newest
  check run per context and treats skipped as satisfied, so a body-only edit
  could turn a red or untested head green. Not triggering at all leaves the
  head's existing check runs authoritative. `tests/unit/test_ci_workflow_required_checks.py`
  pins both halves of this decision.
- **Check context names unchanged.** `Beta release guard` and
  `Stable release guard` report exactly as before. They no longer feed
  `CI Required` (cross-workflow `needs` is impossible). Neither the guards nor
  `CI Required` are in the `protect main` ruleset today, so no enforcement was
  removed; the publish-time guard in `publish-beta-release.yml` is the hard
  gate for tags. To make the beta guard a pre-merge hard gate, require
  `Beta release guard` in the ruleset directly.
- **`push`/`merge_group` kept.** The guards are no-ops without a
  `pull_request` payload, but keeping the events preserves parity with the old
  placement and gives the contexts a report on `main` (a context that has never
  reported cannot be added to the ruleset).

### Failure modes

- A base-branch retarget also arrives as `edited`; it now re-runs only the
  guards. A push or manual re-run refreshes the matrix, and the merge queue
  runs the full suite regardless.
- If the guards ever need the matrix result, do not fold them back into
  `ci.yml`; gate on the separate contexts instead.

## Changed OpenSpec validation

Issue #2032 exposed that canonical-only validation accepts malformed active deltas. The required OpenSpec job also runs `.github/scripts/validate_changed_openspec.py` against GitHub event revisions. PR selection uses the merge base and event head, while validation runs in the normal merge checkout. A target-only invalid change added after a PR branches does not enter that PR's validation set.

Full history makes the merge base available. Disabling rename detection includes both old and new paths; surviving folders are validated, fully removed folders and archive paths are skipped. Strict validation is limited to touched active folders because unrelated legacy deltas can still be invalid. Validator arguments terminate options before the folder name, so a folder named `--help` cannot skip validation.
