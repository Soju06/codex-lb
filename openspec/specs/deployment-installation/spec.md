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

The project MUST run automated Helm smoke installs for the easy-setup install modes in CI. CI Helm smoke installs MUST avoid avoidable external image pulls for chart test pods when the application image has already been built and loaded into the disposable cluster. Smoke scripts MUST emit timestamped logs for major phases so CI output identifies where time is spent. Smoke scripts MUST bound Helm test waits with a configurable timeout.

#### Scenario: Bundled and external DB modes are smoke tested

- **WHEN** CI runs Helm smoke installation checks
- **THEN** it installs the chart on a disposable Kubernetes cluster in bundled mode
- **AND** it installs the chart on a disposable Kubernetes cluster in external DB mode
- **AND** both installs reach a healthy testable state

#### Scenario: CI Helm test uses the loaded application image

- **WHEN** CI runs kind-based Helm smoke checks after loading the application image into the cluster
- **THEN** the Helm test pod image is overridden to the loaded application image
- **AND** the chart default test pod image remains equivalent to `docker.io/library/busybox:1.37` for normal installs

#### Scenario: External DB smoke uses a single app replica

- **WHEN** CI runs the external DB smoke installation
- **THEN** the application release is installed with one replica
- **AND** the smoke still validates external database mode by using an external PostgreSQL release

#### Scenario: Helm smoke phases are timestamped

- **WHEN** CI runs kind-based Helm smoke checks
- **THEN** major phases emit UTC timestamped log lines

#### Scenario: Helm test wait is bounded

- **WHEN** CI runs kind-based Helm smoke checks
- **THEN** each `helm test` invocation uses the configured Helm test timeout
- **AND** the default timeout is shorter than Helm's default wait window

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

### Requirement: Docker Compose Postgres profile

The Docker Compose `postgres` profile SHALL use a persistent named volume for Postgres data.

When the profile uses Postgres 18 or newer, the service SHALL mount that named volume at `/var/lib/postgresql`, the parent directory of the image's versioned `PGDATA` path.

The Compose configuration SHALL provide an explicit one-shot upgrade profile for existing pre-18 named volumes.

The `postgres-upgrade` service SHALL pin the upgrade helper image by digest because the helper mounts the same named Postgres data volume read-write and mutates the stored database cluster.

The normal Postgres service SHALL fail before starting Postgres 18 when it detects a pre-18 root-level `PG_VERSION` marker in the mounted named volume.

The normal Postgres service SHALL fail before starting Postgres 18 when it detects a nested `/var/lib/postgresql/data/PG_VERSION` marker with a pre-18 major version.

The normal Postgres service SHALL preserve runtime command arguments when it delegates to the official Postgres entrypoint.

The operator documentation SHALL describe how to stop the old service, back up the named volume, run the upgrade profile, start Postgres, and verify the upgraded database.

#### Scenario: Existing Postgres 16 volume is guarded

- **GIVEN** the named Compose volume contains a root-level `PG_VERSION` file from a Postgres 16 data directory
- **WHEN** the operator starts the normal `postgres` service after the Postgres 18 upgrade
- **THEN** the service exits before running Postgres
- **AND** the error tells the operator to run the `postgres-upgrade` profile

#### Scenario: Upgraded or fresh Postgres 18 volume starts normally

- **GIVEN** the named Compose volume does not contain a root-level `PG_VERSION` file
- **WHEN** the operator starts the normal `postgres` service
- **THEN** the service delegates to the official Postgres entrypoint
- **AND** the Postgres 18 image initializes or opens the versioned data directory under `/var/lib/postgresql`

#### Scenario: Nested legacy data directory is guarded

- **GIVEN** the named Compose volume contains a nested `/var/lib/postgresql/data/PG_VERSION` file with a pre-18 major version
- **WHEN** the operator starts the normal `postgres` service after the Postgres 18 upgrade
- **THEN** the service exits before running Postgres
- **AND** the error tells the operator that the nested data directory must be upgraded before Postgres 18 starts

#### Scenario: Runtime command arguments are preserved

- **GIVEN** the named Compose volume does not contain a root-level `PG_VERSION` file
- **WHEN** the operator starts the normal `postgres` service with runtime PostgreSQL command arguments
- **THEN** the guard delegates those arguments to the official Postgres entrypoint

