# database-backends Specification

## ADDED Requirements

### Requirement: Greenfield runtime baseline uses PostgreSQL for sustained multi-worker serving

The greenfield runtime baseline MUST use PostgreSQL through CODEX_LB_DATABASE_URL before recommending sustained multi-worker request serving. SQLite MUST remain supported for zero-config and simple local startup, but SQLite MUST NOT be presented as the preferred backend for new sustained deployments.

#### Scenario: Greenfield baseline selects PostgreSQL

- **WHEN** an operator follows the greenfield runtime baseline
- **THEN** the documented database backend is PostgreSQL via CODEX_LB_DATABASE_URL
- **AND** the guidance does not recommend increasing plain request workers on SQLite

#### Scenario: SQLite remains the local startup path

- **WHEN** an operator intentionally stays on SQLite
- **THEN** official guidance treats that as zero-config or local/smoke usage
- **AND** the guidance keeps the single-worker or equivalent write-serialized constraint separate from the greenfield baseline
