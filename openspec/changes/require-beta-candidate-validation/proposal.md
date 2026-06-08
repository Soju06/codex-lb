## Why

A replacement release PR merged beta version metadata from a non-canonical `fix/*`
branch. That left `main` saying `1.20.0-beta.3` while the beta publish workflow
did not create `v1.20.0-beta.3` artifacts because publishing is gated on
`release/beta-*` branches. More importantly, beta artifacts must never be
published merely because version metadata reached `main`; the exact release
candidate must be validated first.

## What Changes

- Add a CI beta release guard for pull requests that change release-managed
  version files to a beta version.
- Require beta metadata PRs to keep all release-managed version files consistent,
  come from the canonical repository's `release/beta-X.Y.Z-beta.N` branch, and
  include checked release-candidate validation evidence for the exact PR head SHA.
- Require the published merge commit tree to match that validated PR head, so a
  stale candidate cannot publish after `main` advances.
- Re-run CI on PR body edits so completing or refreshing the validation checklist
  updates the required guard result.
- Add the same validation evidence check to the beta publish workflow before it
  creates tags or GitHub prereleases.
- Add an unchecked validation checklist to automation-generated beta release PRs
  so the PR cannot merge or publish until validation is recorded.

## Capabilities

### Modified Capabilities

- Release management: beta release PRs become candidate-validation gated.
- Release automation: beta publish refuses unvalidated, stale-tree, forked, or
  otherwise non-canonical beta PRs.

## Impact

- Code: `scripts/guard_beta_release.py`.
- Workflows: `.github/workflows/ci.yml`, `.github/workflows/prepare-beta-release.yml`,
  `.github/workflows/publish-beta-release.yml`.
- Tests: `tests/unit/test_guard_beta_release.py`.
