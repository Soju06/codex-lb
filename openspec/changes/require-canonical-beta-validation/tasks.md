# Tasks

- [x] Add regression coverage for a canonical beta PR whose release metadata is unchanged against the base branch but whose validation evidence is missing
- [x] Update `scripts.guard_beta_release` so canonical beta PRs require validation evidence before no-op metadata short-circuiting
- [x] Document the release-management requirement delta
- [x] Run `openspec validate require-canonical-beta-validation --strict`
- [x] Run targeted guard tests
- [x] Run lint/type gates for the touched Python code
