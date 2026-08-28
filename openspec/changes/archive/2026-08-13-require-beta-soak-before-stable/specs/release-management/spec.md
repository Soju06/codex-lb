# release-management Delta

## MODIFIED Requirements

### Requirement: Stable release promotion remains release-please owned

A beta-tested release train SHALL be promoted by merging the normal
release-please stable release PR for the corresponding base version. Stable
promotion SHALL rebuild PyPI, Docker, Helm, and GitHub Release artifacts with
the stable version instead of retagging prerelease artifacts.

Before the stable release PR for `X.Y.Z` is merged, every change in the
release candidate SHALL either be covered by a `vX.Y.Z-beta.N` prerelease
that has been published and deployed to at least one production-scale
environment for a soak of at least 48 hours without new regressions
attributable to the release train, or fall under the safe-delta exception
below. The unsoaked delta — every change not covered by such a soaked
prerelease, whether because no prerelease of the train completed a soak or
because the change landed after the last soaked prerelease — qualifies for
the exception only when it consists solely of documentation, CI, or
release-tooling changes, an urgent security or outage hotfix, or a
combination of these; otherwise the train SHALL soak (again) as a new
prerelease before stable promotion. When promotion relies on the exception,
the exception and its reason SHALL be recorded on the stable release PR
before merge.

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
- **AND** the entire delta since `v1.21.0` consists of an urgent security
  hotfix and CI changes only
- **WHEN** a maintainer merges the stable release PR for `1.21.1` with the
  exception and its reason recorded on the PR
- **THEN** the promotion is compliant with this requirement

#### Scenario: unrelated unsoaked changes cannot ride a hotfix exception

- **GIVEN** no `v1.22.0-beta.N` prerelease has completed a 48-hour soak
- **AND** the delta since the previous stable release contains an urgent
  hotfix alongside unrelated feature or migration changes
- **WHEN** a maintainer considers promoting `1.22.0` directly to stable
- **THEN** the hotfix exception does not apply to the train
- **AND** the train either soaks as a beta or the hotfix is released
  separately

#### Scenario: changes landing after the soaked beta restart the soak

- **GIVEN** `v1.23.0-beta.1` completed a 48-hour production-scale soak
- **AND** a feature or migration change lands on `main` afterwards, before
  the stable release PR for `1.23.0` is merged
- **WHEN** a maintainer considers promoting `1.23.0` to stable
- **THEN** the post-beta change is part of the unsoaked delta
- **AND** because a feature or migration change is not exception-eligible,
  promotion requires a new soaked prerelease covering it

#### Scenario: data backfills are identified from Alembic revisions

- **GIVEN** the release train adds revisions under `app/db/alembic/versions`
  since the previous stable tag
- **WHEN** the maintainer prepares to merge the stable release PR
- **THEN** they review those revisions directly for data-backfill operations
- **AND** changelog titles alone are not treated as evidence that no backfill
  is present
