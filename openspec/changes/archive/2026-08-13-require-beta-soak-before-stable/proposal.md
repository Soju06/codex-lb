# Change: require-beta-soak-before-stable

## Why

The beta channel (prepare/publish-beta-release, release-candidate validation
evidence, prerelease-only Docker aliases) and the stable release guards all
exist, but the stable-promotion requirement in `release-management` says only
that "a beta-tested release train SHALL be promoted" — it defines neither what
"beta-tested" means (no soak duration) nor an exception path. Anyone driving a
release (maintainer or assistant) can therefore cut stable directly while
remaining spec-compliant. v1.22.0 was promoted without any `v1.22.0-beta.N`,
and its startup-blocking data-backfill migration surfaced during a production
rollout instead of during a beta soak.

Separately, the generated changelog cannot identify backfill migrations
(Conventional Commit titles don't mention revision contents), so a pre-merge
check based on the changelog misses exactly the operations it exists to catch.

## What Changes

- `release-management`: stable promotion of `X.Y.Z` requires a published
  `vX.Y.Z-beta.N` soaked on at least one production-scale deployment for at
  least 48 hours, with a recorded maintainer exception path (docs/CI/release-
  tooling-only deltas; urgent security or outage hotfixes).
- `release-management`: before merging a stable release PR whose train
  contains Alembic revisions, the revisions between the previous stable tag
  and the candidate are reviewed directly for data backfills; changelog titles
  are not sufficient evidence.
- `.github/CONTRIBUTING.md` gains a matching "Release channels: beta first"
  subsection (same pull request).

## Impact

Process/documentation change only; no runtime behavior change. Enforcement in
this change is maintainer review. CI aids (e.g., automatically labelling
release PRs whose train contains data-backfill revisions, and a
production-scale migration duration gate) are tracked separately.
