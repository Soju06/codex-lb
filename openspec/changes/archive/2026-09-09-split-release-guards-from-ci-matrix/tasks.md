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
- [x] 3.3 After merge: confirm a PR body edit creates only a `Release guards` run, and that `Beta release guard` reports on `main`. _(verified 2026-09-09 at archive, after #2212 merged 02:35Z: PR #2221 head `d3bc5a7b3` got Release guards runs 34320491164 (06:45:07Z, push) and 34320529966 (06:45:35Z, body edit) but a single CI run 34320491226 (06:45:07Z); PR #2224 intermediate head `cf38d55ff` got Release guards runs 34315808968 and 34316652233 after the 05:39:42Z push with a single CI run 34315776443; `main` push `09b48eb27` produced Release guards run 34327430320 reporting `Beta release guard: success` and `Stable release guard: success`.)_
