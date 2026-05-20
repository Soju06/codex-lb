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

### Requirement: Application data directory resolution is configurable and container-aware

The application MUST resolve its default data directory from operator intent before container heuristics. A non-empty `CODEX_LB_DATA_DIR` value MUST be the highest-priority data directory override. When no override is configured, an existing `$HOME/.codex-lb` directory MUST remain preferred even if the process detects that it is running inside a container. The container data directory (`/var/lib/codex-lb`) MUST be used only when no override is configured, the home data directory does not already exist, and container detection is true.

#### Scenario: Explicit data directory override wins

- **GIVEN** `CODEX_LB_DATA_DIR` is configured to a non-empty path
- **WHEN** application settings are loaded
- **THEN** the configured path is used as the data directory
- **AND** the container detection result does not override it

#### Scenario: Existing home data is reused inside an interactive container

- **GIVEN** `CODEX_LB_DATA_DIR` is not configured
- **AND** `$HOME/.codex-lb` already exists
- **AND** container detection is true
- **WHEN** application settings are loaded
- **THEN** `$HOME/.codex-lb` is used as the data directory
- **AND** `/var/lib/codex-lb` is not selected

#### Scenario: Container default is preserved when no home data exists

- **GIVEN** `CODEX_LB_DATA_DIR` is not configured
- **AND** `$HOME/.codex-lb` does not exist
- **AND** container detection is true
- **WHEN** application settings are loaded
- **THEN** `/var/lib/codex-lb` is used as the data directory

#### Scenario: Related default paths follow the resolved data directory

- **GIVEN** the resolved data directory differs from the module-import default
- **AND** the database URL, encryption key file, conversation archive directory, and response-create dump directory are not explicitly configured
- **WHEN** application settings and proxy dump helpers are used
- **THEN** the default SQLite database URL points at `<data-dir>/store.db`
- **AND** the default encryption key file points at `<data-dir>/encryption.key`
- **AND** the default conversation archive directory points at `<data-dir>/conversation-archive`
- **AND** oversized response-create dumps are written under `<data-dir>/debug/response-create-dumps`

#### Scenario: Explicit related path overrides are preserved

- **GIVEN** `CODEX_LB_DATA_DIR` is configured
- **AND** one or more related paths such as `CODEX_LB_DATABASE_URL`, `CODEX_LB_ENCRYPTION_KEY_FILE`, or `CODEX_LB_CONVERSATION_ARCHIVE_DIR` are explicitly configured
- **WHEN** application settings are loaded
- **THEN** each explicitly configured related path keeps its configured value
- **AND** only omitted related paths derive from the resolved data directory



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

### Requirement: Greenfield Docker runtime baseline is explicit and bridge-safe

Docker and operator installation guidance MUST define a greenfield runtime baseline for new sustained deployments. The baseline MUST be PostgreSQL-backed, MUST keep the standard `2455` API port and `1455` OAuth callback port, and MUST use a bridge-safe worker strategy when the HTTP Responses session bridge is enabled.

#### Scenario: Greenfield baseline combines PostgreSQL and worker settings

- **WHEN** an operator follows the greenfield runtime baseline
- **THEN** the example configures `CODEX_LB_DATABASE_URL` for PostgreSQL
- **AND** it configures request-worker concurrency only through a bridge-safe startup path when the HTTP Responses session bridge remains enabled
- **AND** it keeps the standard listener and OAuth callback ports unchanged

#### Scenario: Plain multi-worker guidance requires bridge disabled

- **WHEN** the guidance shows plain Uvicorn multi-worker serving
- **THEN** it requires the HTTP Responses session bridge to be disabled
- **AND** it does not present plain multi-worker serving with a shared bridge instance id as a valid greenfield baseline

### Requirement: Greenfield baseline preserves operator tier preference

Operator guidance for tiered traffic in the greenfield baseline MUST preserve the operator's selected Codex CLI service tier. The guidance MUST NOT recommend changing an existing ultrafast preference solely because the runtime is busy. The guidance MUST instead require verification of requested tier versus actual upstream served tier.

#### Scenario: Greenfield baseline verifies ultrafast

- **WHEN** an operator uses Codex CLI with service_tier set to ultrafast
- **THEN** the greenfield baseline keeps that requested tier unchanged
- **AND** provides a verification path that compares requested_service_tier to actual_service_tier

### Requirement: Helm PostgreSQL helpers track the PostgreSQL runtime major

The Helm chart MUST use PostgreSQL client images on the same major runtime
baseline as bundled database coverage for migration and database initialization
helpers.

#### Scenario: Hook helper images use PostgreSQL 18

- **WHEN** the Helm chart renders the migration job with bundled PostgreSQL
  enabled
- **THEN** the wait-for-db init container uses a PostgreSQL 18 image
- **AND** the database initialization job uses a PostgreSQL 18 image
