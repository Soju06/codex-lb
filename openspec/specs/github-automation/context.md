# Context: github-automation

Normative requirements live in [`spec.md`](./spec.md). This document currently
covers the Simplicity budgets check; the codex-review label-sync machinery is
summarized in the spec's Purpose.

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
