# deployment-installation Specification

## Purpose

Define installation modes and smoke-test expectations so the Helm chart remains portable across supported deployments.
## Requirements
### Requirement: Helm chart is organized around install modes

The Helm chart MUST document and support three primary install modes: bundled PostgreSQL, direct external database, and external secrets. These install contracts MUST be portable across Kubernetes providers without requiring provider-specific chart forks.

#### Scenario: Bundled mode values exist

- **WHEN** a user wants a self-contained install
- **THEN** the chart provides a bundled mode values overlay with bundled PostgreSQL enabled

#### Scenario: External DB mode values exist

- **WHEN** a user wants to install against an already reachable PostgreSQL database
- **THEN** the chart provides an external DB values overlay and accepts direct DB URL or DB secret wiring

#### Scenario: External secrets mode values exist

- **WHEN** a user wants to source credentials from External Secrets Operator
- **THEN** the chart provides an external secrets values overlay that keeps migration and startup behavior fail-closed

### Requirement: Helm install modes are smoke-tested

The project MUST run automated Helm smoke installs for the easy-setup install modes in CI.

#### Scenario: Bundled and external DB modes are smoke tested

- **WHEN** CI runs Helm smoke installation checks
- **THEN** it installs the chart on a disposable Kubernetes cluster in bundled mode
- **AND** it installs the chart on a disposable Kubernetes cluster in external DB mode
- **AND** both installs reach a healthy testable state

### Requirement: Helm support policy is pinned to modern Kubernetes minors

The chart MUST declare a minimum supported Kubernetes version of `1.32`, and CI MUST validate chart rendering against a `1.35` baseline instead of older legacy minors.

#### Scenario: Chart metadata declares the minimum supported version

- **WHEN** a user inspects the chart metadata and README
- **THEN** the documented minimum supported Kubernetes version is `1.32`

#### Scenario: CI validates the modern baseline

- **WHEN** CI runs Kubernetes schema validation and kind-based smoke installs
- **THEN** the validation set includes Kubernetes `1.35`
- **AND** pre-`1.32` validation targets are not treated as the support baseline

### Requirement: Direct Docker respects addressable worker-pool startup

The direct Docker helper MUST rebuild the current checkout and recreate the local container without forcing the runtime worker count to one. If the env file configures multiple workers while the HTTP responses session bridge is enabled, the image startup path MUST use the addressable bridge worker pool rather than a plain multi-worker Uvicorn process.

#### Scenario: direct Docker uses env-file worker count

- **WHEN** an operator runs the local Docker helper
- **AND** `.env.local` configures `CODEX_LB_UVICORN_WORKERS` greater than one
- **THEN** the helper does not add a conflicting `CODEX_LB_UVICORN_WORKERS=1` override
- **AND** the container startup path is responsible for selecting the safe worker-pool runtime

### Requirement: Docker guidance includes a SQLite-conservative profile

Docker installation guidance MUST include a SQLite-conservative runtime profile for operators who intentionally stay on SQLite. This profile MUST be distinct from the PostgreSQL higher-concurrency profile.

#### Scenario: SQLite-conservative Docker profile limits request workers

- **WHEN** an operator follows the documented SQLite-conservative Docker profile
- **THEN** the example keeps the SQLite database URL
- **AND** the example configures a single request worker or an equivalent write-serialized runtime
- **AND** the guidance explains that this profile trades throughput for fewer SQLite writer-lock failures

#### Scenario: Higher-concurrency Docker guidance remains PostgreSQL-backed

- **WHEN** an operator needs sustained multi-worker throughput
- **THEN** Docker guidance points to the PostgreSQL-backed profile rather than recommending unconstrained multi-worker SQLite
- **AND** the standard listener and OAuth callback ports remain unchanged

### Requirement: Docker installation documents a PostgreSQL performance profile

Docker-based installation guidance MUST provide a PostgreSQL-backed path for operators who need throughput beyond the default SQLite profile. This guidance MUST preserve the existing SQLite-first quick start for simple local usage.

#### Scenario: Docker quick start remains SQLite-first

- **WHEN** an operator follows the default Docker quick-start flow
- **THEN** the documented path continues to use the SQLite-backed default storage path
- **AND** PostgreSQL is not required for basic local startup

#### Scenario: Docker performance profile uses PostgreSQL

- **WHEN** an operator wants the documented higher-throughput Docker deployment profile
- **THEN** the guidance provides a PostgreSQL-backed example using `CODEX_LB_DATABASE_URL`
- **AND** the example keeps the standard `2455` and `1455` service ports
- **AND** the guidance identifies PostgreSQL as the recommended backend for that profile

