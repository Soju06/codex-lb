# database-backends Specification

## Purpose

Define supported database backend wiring so local, Helm, SQLite, and external PostgreSQL deployments behave consistently.
## Requirements
### Requirement: Helm external PostgreSQL wiring resolves a non-empty database URL

When the Helm chart deploys with `postgresql.enabled=false`, it MUST provide a non-empty `CODEX_LB_DATABASE_URL` to the workload from one of the supported external database inputs. The chart MUST accept a direct `externalDatabase.url`, and it MUST also support reading `database-url` from an operator-provided external database secret reference without requiring the application encryption-key secret to be the same object.

#### Scenario: Direct external database URL is used

- **WHEN** `postgresql.enabled=false`
- **AND** `externalDatabase.url` is non-empty
- **THEN** the rendered workload uses that value for `CODEX_LB_DATABASE_URL`

#### Scenario: External database URL comes from a dedicated secret reference

- **WHEN** `postgresql.enabled=false`
- **AND** `externalDatabase.existingSecret` is set
- **THEN** the rendered workload reads `database-url` from that secret for `CODEX_LB_DATABASE_URL`

### Requirement: SQLite live profile controls write contention

When `codex-lb` is configured to use SQLite for live serving, official behavior and guidance MUST treat SQLite as a single-writer backend and MUST provide a supported operating profile that limits concurrent write contention without requiring PostgreSQL.

#### Scenario: SQLite live profile is conservative by default

- **WHEN** an operator chooses SQLite for live Docker or long-running local serving
- **THEN** the supported SQLite profile limits runtime write concurrency through a single request worker, a single writer, or an equivalent serialization mechanism
- **AND** the profile preserves the existing SQLite database URL format

#### Scenario: Multi-worker SQLite requires explicit write hardening

- **WHEN** SQLite is used with more than one request worker
- **THEN** write-hot paths serialize writes or apply bounded lock retry/backoff before returning a server error
- **AND** the runtime does not rely on SQLite busy timeout alone as the only mitigation for expected concurrent writer overlap

### Requirement: SQLite lock pressure is retried on write-hot paths

Write-hot SQLite operations that are part of request admission, usage reservation, bridge ownership, or liveness bookkeeping MUST handle transient SQLite lock errors with bounded retry/backoff before returning a 5xx response.

#### Scenario: Usage reservation write hits a transient SQLite lock

- **WHEN** a usage reservation insert or update fails because SQLite reports the database is locked
- **THEN** the operation is retried with bounded backoff while the request budget remains available
- **AND** the final failure, if any, is logged with enough context to distinguish lock exhaustion from non-retryable database errors

#### Scenario: Bridge liveness write hits a transient SQLite lock

- **WHEN** a bridge or ring heartbeat write fails because SQLite reports the database is locked
- **THEN** the liveness write is retried with bounded backoff or deferred through the configured SQLite write-serialization path
- **AND** transient lock pressure does not immediately mark healthy bridge workers as failed

### Requirement: Higher-concurrency deployments recommend PostgreSQL

`codex-lb` MUST preserve SQLite as the default backend for zero-config and simple local startup, and it MUST document PostgreSQL as the recommended backend for deployments that expect higher concurrency, write-heavy traffic, or sustained multi-worker serving.

#### Scenario: Zero-config startup keeps SQLite as the default

- **WHEN** an operator starts `codex-lb` without setting `CODEX_LB_DATABASE_URL`
- **THEN** the runtime uses the default SQLite-backed database path
- **AND** official guidance presents SQLite as the convenience default for simple local startup

#### Scenario: Higher-concurrency guidance recommends PostgreSQL

- **WHEN** an operator configures `codex-lb` for higher concurrency, sustained multi-worker traffic, or infrastructure-managed serving
- **THEN** official guidance recommends PostgreSQL via `CODEX_LB_DATABASE_URL`
- **AND** the guidance does not require changing unrelated listener or OAuth callback port defaults

### Requirement: Greenfield runtime baseline uses PostgreSQL for sustained multi-worker serving

The greenfield runtime baseline MUST use PostgreSQL through `CODEX_LB_DATABASE_URL` before recommending sustained multi-worker request serving. SQLite MUST remain supported for zero-config and simple local startup, but SQLite MUST NOT be presented as the preferred backend for new sustained deployments.

#### Scenario: Greenfield baseline selects PostgreSQL

- **WHEN** an operator follows the greenfield runtime baseline
- **THEN** the documented database backend is PostgreSQL via `CODEX_LB_DATABASE_URL`
- **AND** the guidance does not recommend increasing plain request workers on SQLite

#### Scenario: SQLite remains the local startup path

- **WHEN** an operator intentionally stays on SQLite
- **THEN** official guidance treats that as zero-config or local/smoke usage
- **AND** the guidance keeps the single-worker or equivalent write-serialized constraint separate from the greenfield baseline
