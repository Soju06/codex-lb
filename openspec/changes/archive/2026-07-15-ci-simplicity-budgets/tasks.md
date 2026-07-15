# Tasks: ci-simplicity-budgets

## 1. Budget manifest and checker

- [x] 1.1 Add `.github/simplicity-budgets.toml` with the `[readme]`
      (max_lines counted after stripping the all-contributors block,
      max_top_level_headings fence-aware), `[env_example]` (max_lines), and
      `[core_nav]` (path/array/max_items) budgets
- [x] 1.2 Add `.github/scripts/check_simplicity_budgets.py` (stdlib-only):
      contributors-block stripping, fence-aware h1+h2 counting, nav `to:`
      counting with exit 2 when the configured nav file/array is missing,
      `PR_LABELS` JSON override via the `simplicity-budget-approved` label,
      and a failure message covering the label path, the live label fetch,
      and the merge_group bump-the-TOML policy

## 2. Workflow

- [x] 2.1 Add `.github/workflows/simplicity-budgets.yml`: separate workflow
      on `pull_request` `[opened, reopened, synchronize, labeled, unlabeled]`,
      `push` to `main`, and `merge_group`; `contents: read`; SHA-pinned
      checkout with `persist-credentials: false`; `PR_LABELS` passed as
      `toJson(github.event.pull_request.labels.*.name)`; header comment
      explaining why the triggers differ from ci.yml; no `paths:` filter

## 3. Tests

- [x] 3.1 Add `tests/unit/test_check_simplicity_budgets.py`: fence-aware
      counting, contributors-block stripping, over/under budget exit codes,
      label override, null-payload handling, and nav file/array-missing exit 2

## 4. Validation

- [x] 4.1 `uv run pytest tests/unit/test_check_simplicity_budgets.py -q`
- [x] 4.2 Run the checker against the repository tree; all budgets pass
- [x] 4.3 `uv run ruff check` / `ruff format --check` on the new script and test
- [x] 4.4 `openspec validate ci-simplicity-budgets --strict` and
      `openspec validate --specs`
