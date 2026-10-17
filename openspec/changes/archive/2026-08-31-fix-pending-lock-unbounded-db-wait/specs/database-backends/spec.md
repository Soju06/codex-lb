## MODIFIED Requirements

### Requirement: PostgreSQL engines validate and recycle pooled connections

When `database_url` resolves to a PostgreSQL backend, the application MUST configure each async engine — both the request-path `engine` and the optional background-task `_background_engine` — with `pool_pre_ping=True` and a finite `pool_recycle` window. This is required so the application detects connections that the PostgreSQL server has silently closed (idle timeout, restart, network reset) before the first real query is dispatched on them, and so connections are cycled before they reach any reasonable upstream keep-alive boundary. The recycle window is the fixed 1800-second application constant in `app/db/session.py`.

Each PostgreSQL statement MUST additionally be bounded by the fixed asyncpg `command_timeout` application constant in `app/db/session.py`, so a query stalled on a half-dead connection surfaces as an error within the bound instead of awaiting indefinitely — pre-ping only protects the first statement after checkout, not a connection that dies mid-statement. A statement that dies mid-flight while its caller holds an application lock MUST therefore release that caller within the bound. Alembic migrations run on their own synchronous engine and are not subject to this bound.

#### Scenario: Stale connections are rejected before checkout

- **WHEN** a pooled connection has been closed by the server while sitting idle
- **AND** that connection is the next one a session tries to use
- **THEN** SQLAlchemy issues a pre-ping (`SELECT 1`), detects the dead connection, and transparently replaces it
- **AND** the application returns `200` (or the real business-level result), not `500 server_error` with `asyncpg.InterfaceError: connection is closed`

#### Scenario: Pool recycle bounds connection age

- **WHEN** a pooled connection has been open longer than the fixed 1800-second recycle window
- **AND** that connection is the next one a session tries to use
- **THEN** SQLAlchemy discards and replaces the connection before the next query

#### Scenario: Mid-statement connection death cannot wedge its caller

- **WHEN** a statement's connection dies after dispatch (network partition, half-dead peer) and no response arrives
- **THEN** asyncpg cancels the statement and raises within the fixed `command_timeout` bound
- **AND** any application lock held by the caller is released within that bound instead of being held indefinitely

#### Scenario: SQLite backends are not affected

- **WHEN** `database_url` resolves to a SQLite backend (file or `:memory:`)
- **THEN** neither `pool_pre_ping`, `pool_recycle`, nor `command_timeout` is configured on the engine
- **AND** existing SQLite-specific tuning (PRAGMAs, `busy_timeout`) is unchanged
