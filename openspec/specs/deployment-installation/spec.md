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

