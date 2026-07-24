# release-management Delta

## MODIFIED Requirements

### Requirement: Stable release promotion remains release-please owned

A beta-tested release train SHALL be promoted by merging the normal
release-please stable release PR for the corresponding base version. Stable
promotion SHALL rebuild PyPI, Docker, Helm, and GitHub Release artifacts with
the stable version instead of retagging prerelease artifacts.

Before the stable release PR for `X.Y.Z` is merged, a `vX.Y.Z-beta.N`
prerelease SHALL have been published and deployed to at least one
production-scale environment for a soak of at least 48 hours without new
regressions attributable to the release train. A maintainer MAY promote
directly to stable without a completed soak only when the delta since the last
soaked prerelease of the train consists solely of documentation, CI, or
release-tooling changes, or when the promotion is an urgent security or outage
hotfix; the exception and its reason SHALL be recorded on the stable release
PR before merge.

When the release train contains Alembic revisions, the maintainer SHALL review
the revisions between the previous stable tag and the release candidate
directly for data-backfill migrations and SHALL estimate their startup impact
against a production-scale dataset before merging the stable release PR.
Generated changelog titles SHALL NOT be treated as sufficient evidence that
the train contains no data backfills.

#### Scenario: beta train is promoted to stable

- **GIVEN** `v1.19.0-beta.2` was published from `main`
- **AND** release-please has prepared the stable release PR for `1.19.0`
- **WHEN** the stable release PR is merged
- **THEN** release-please creates the stable `v1.19.0` release
- **AND** the release publishing workflow publishes stable artifacts for `1.19.0`
- **AND** stable Docker aliases `latest`, `1`, and `1.19` are updated only by the stable release

#### Scenario: stable promotion waits for the beta soak

- **GIVEN** `v1.20.0-beta.1` was published 12 hours ago and is deployed on a
  production-scale environment
- **WHEN** a maintainer considers merging the stable release PR for `1.20.0`
- **THEN** promotion waits until the beta has soaked for at least 48 hours
  without new regressions attributable to the release train

#### Scenario: stable promotion without a soaked beta records an exception

- **GIVEN** no `v1.21.1-beta.N` prerelease has completed a 48-hour soak
- **AND** the release train is an urgent security hotfix
- **WHEN** a maintainer merges the stable release PR for `1.21.1` with the
  exception and its reason recorded on the PR
- **THEN** the promotion is compliant with this requirement

#### Scenario: data backfills are identified from Alembic revisions

- **GIVEN** the release train adds revisions under `app/db/alembic/versions`
  since the previous stable tag
- **WHEN** the maintainer prepares to merge the stable release PR
- **THEN** they review those revisions directly for data-backfill operations
- **AND** changelog titles alone are not treated as evidence that no backfill
  is present
