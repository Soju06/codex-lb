# deployment-installation Specification

## ADDED Requirements

### Requirement: Greenfield Docker runtime baseline is explicit and bridge-safe

Docker and operator installation guidance MUST define a greenfield runtime baseline for new sustained deployments. The baseline MUST be PostgreSQL-backed, MUST keep the standard 2455 API port and 1455 OAuth callback port, and MUST use a bridge-safe worker strategy when the HTTP Responses session bridge is enabled.

#### Scenario: Greenfield baseline combines PostgreSQL and worker settings

- **WHEN** an operator follows the greenfield runtime baseline
- **THEN** the example configures CODEX_LB_DATABASE_URL for PostgreSQL
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
