## ADDED Requirements

### Requirement: Backend area detection covers the native egress packaging inputs

The `backend` entry of `.github/scripts/detect_changed_areas.py` `FILTERS` MUST
match every repository path the backend unit suite reads when it asserts on
native-egress packaging: the Rust workspace manifest and lockfile
(`Cargo.toml`, `Cargo.lock`), the workspace sources (`crates/**`), and the
container build files (`Dockerfile`, `Dockerfile.*`). A pull request that
touches only those paths MUST report the `backend` area as changed, so the
pytest matrix runs its `make test-*` targets instead of the placeholder step
that keeps the required contexts green.

#### Scenario: Rust workspace change runs the backend suite

- **GIVEN** a pull request that changes only `Cargo.toml`, `Cargo.lock` or a file under `crates/`
- **WHEN** change detection runs
- **THEN** the `backend` area is reported as changed
- **AND** the pytest matrix runs its `make test-*` targets for that pull request

#### Scenario: Container build file change runs the backend suite

- **GIVEN** a pull request that changes only `Dockerfile` or `Dockerfile.distroless`
- **WHEN** change detection runs
- **THEN** the `backend` area is reported as changed
