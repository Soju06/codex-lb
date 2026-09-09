## 1. Split the workflows

- [x] 1.1 Remove `edited` from `ci.yml`'s `pull_request` types and replace the header rationale.
- [x] 1.2 Move `beta-release-guard` and `stable-release-guard` into `release-guards.yml` with `edited`, its own concurrency group, and unchanged job names.
- [x] 1.3 Drop `beta-release-guard` from the `ci-required` aggregate.

## 2. Guard the split

- [x] 2.1 Add unit tests asserting `ci.yml` never subscribes to `edited` and the guards live in `release-guards.yml`.
- [x] 2.2 Update `github-automation` spec/context and `CONTRIBUTING.md` merge-gate wording.

## 3. Verification

- [x] 3.1 YAML parse + actionlint on both workflows; `uv run pytest tests/unit/test_ci_workflow_required_checks.py`.
- [x] 3.2 Confirm the ruleset's required contexts are all still produced (`gh api repos/Soju06/codex-lb/rules/branches/main`).
- [ ] 3.3 After merge: confirm a PR body edit creates only a `Release guards` run, and that `Beta release guard` reports on `main`.
