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

### Requirement: PostgreSQL engines validate and recycle pooled connections

When `database_url` resolves to a PostgreSQL backend, the application MUST configure each async engine — both the request-path `engine` and the optional background-task `_background_engine` — with `pool_pre_ping=True` and a finite `pool_recycle` window. This is required so the application detects connections that the PostgreSQL server has silently closed (idle timeout, restart, network reset) before the first real query is dispatched on them, and so connections are cycled before they reach any reasonable upstream keep-alive boundary.

#### Scenario: Stale connections are rejected before checkout

- **WHEN** a pooled connection has been closed by the server while sitting idle
- **AND** that connection is the next one a session tries to use
- **THEN** SQLAlchemy issues a pre-ping (`SELECT 1`), detects the dead connection, and transparently replaces it
- **AND** the application returns `200` (or the real business-level result), not `500 server_error` with `asyncpg.InterfaceError: connection is closed`

#### Scenario: Pool recycle bounds connection age

- **WHEN** a pooled connection has been open longer than `database_pool_recycle_seconds`
- **AND** that connection is the next one a session tries to use
- **THEN** SQLAlchemy discards and replaces the connection before the next query
- **AND** the default `database_pool_recycle_seconds` is `1800` seconds

#### Scenario: SQLite backends are not affected

- **WHEN** `database_url` resolves to a SQLite backend (file or `:memory:`)
- **THEN** neither `pool_pre_ping` nor `pool_recycle` is configured on the engine
- **AND** existing SQLite-specific tuning (PRAGMAs, `busy_timeout`) is unchanged

### Requirement: Database pool controls cover request-adjacent background sessions
The service SHALL expose database pool settings for both the main request pool
and the background/request-adjacent session pool. The background pool SHALL
default to the main pool size and overflow settings, and operators MAY override
the background pool size and overflow separately.

#### Scenario: Background pool inherits main pool capacity
- **WHEN** `database_background_pool_size` and `database_background_max_overflow` are unset
- **THEN** the background/request-adjacent DB pool uses `database_pool_size` and `database_max_overflow`

#### Scenario: Background pool has explicit lower capacity
- **WHEN** `database_background_pool_size` and `database_background_max_overflow` are configured
- **THEN** the background/request-adjacent DB pool uses those explicit values

