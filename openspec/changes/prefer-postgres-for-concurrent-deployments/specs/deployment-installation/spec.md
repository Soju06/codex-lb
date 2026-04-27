# deployment-installation Specification

## ADDED Requirements

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
