# Change: ci-simplicity-budgets

## Why

The simplicity effort (see the `contribution-simplicity` capability introduced
by the `codify-simplicity-principles` change) restores the project's 1-click
promise: a short README, a minimal `.env.example`, and a narrow dashboard core
nav. Without a mechanical gate those documents regrow — the README had reached
652 lines and `.env.example` 115 lines before the docs-site diet. A budget
check in CI turns "keep it simple" from a review opinion into a cheap,
deterministic merge gate whose escape hatch is itself a reviewable one-line
diff.

## What Changes

- Add `.github/simplicity-budgets.toml`: budget numbers in data, not code —
  README max lines (counted after stripping the generated all-contributors
  block) and max top-level headings, `.env.example` max lines, and the
  dashboard core-nav max item count with the nav file/array it is read from.
- Add `.github/scripts/check_simplicity_budgets.py` (stdlib-only, runs on the
  runner's `python3`): fence-aware heading counting, contributors-block
  stripping, nav-array item counting that exits 2 loudly when the configured
  file/array is missing (so nav refactors must repoint the TOML), and a
  `simplicity-budget-approved` PR-label override that downgrades violations to
  warning annotations.
- Add `.github/workflows/simplicity-budgets.yml`: a separate seconds-long
  workflow on `pull_request` (including `labeled`/`unlabeled` so applying the
  override label starts a fresh run), `push` to `main`, and `merge_group`.
  Kept out of `ci.yml` because label churn from the Codex label sync would
  re-run the full CI matrix.
- Add unit tests for the checker at `tests/unit/test_check_simplicity_budgets.py`.

## Impact

- Affected specs: `github-automation` (new simplicity-budget gate requirements)
- Affected code: `.github/simplicity-budgets.toml`,
  `.github/scripts/check_simplicity_budgets.py`,
  `.github/workflows/simplicity-budgets.yml`,
  `tests/unit/test_check_simplicity_budgets.py`
- Operator actions (out of band): create the `simplicity-budget-approved`
  label once; add the `Simplicity budgets` check to the required-checks
  ruleset only after the workflow has produced at least one green run on
  `main`. The check never joins ci.yml's `ci-required` aggregate
  (cross-workflow `needs` is impossible; the label triggers must stay out of
  ci.yml).
- Ordering: merges after the docs-site diet (README/.env.example already
  within budget); the later dashboard nav refactor must repoint `[core_nav]`
  in its own diff — the checker's exit-2 behavior makes forgetting impossible.
