## Why

The repository ruleset requires the four `Tests (pytest, <slice>)` matrix check
contexts. The CI workflow skipped the entire pytest matrix job for PRs whose
changed files did not match the backend path filter. In that state GitHub did
not create the matrix check contexts, so branch protection left those required
contexts in an expected/missing state even though the aggregate `CI Required`
job and every relevant job were green.

## What Changes

- Keep the pytest matrix job instantiated for every pull request.
- For non-backend changes, make each pytest matrix slice run a cheap successful
  placeholder step instead of skipping the job at the job level.
- Keep the real pytest setup and `make test-*` steps gated on backend changes.
- Add regression coverage that asserts the required pytest contexts can be
  created for non-backend PRs.

## Capabilities

### Modified Capabilities

- GitHub automation: CI required check contexts are stable across path-filtered
  pull requests.

## Impact

- Workflow: `.github/workflows/ci.yml`.
- Tests: `tests/unit/test_ci_workflow_required_checks.py`.
