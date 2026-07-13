## Why

After merging a beta release PR (e.g. beta.2), the Sync Beta Release PR
workflow immediately opens the next beta PR (beta.3) even when zero meaningful
commits exist between the latest beta tag and HEAD. This creates a releasable
PR with no content, unlike release-please which only opens or updates its PR
when Conventional Commit changes accumulate on main.

Additionally, the beta PR body contains a static template instead of an
accumulated changelog, making it hard to tell what a given beta release will
ship before merging the PR.

## What Changes

- `scripts/prepare_beta_release.py` skips beta PR creation when no releasable
  Conventional Commits exist since the latest beta tag.
- `prepare-beta-release.yml` includes a generated changelog section in the beta
  PR body.
- A beta PR body is updated on each subsequent main push that adds releasable
  commits.

## Capabilities

### Modified Capabilities

- CI/CD release automation: beta release sync workflow gains content-gated
  creation and changelog body generation.

## Impact

- Code: `scripts/prepare_beta_release.py`, `scripts/release_versions.py`,
  `.github/workflows/prepare-beta-release.yml`.
- Tests: `tests/unit/test_release_versions.py`.
