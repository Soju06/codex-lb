## ADDED Requirements

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
