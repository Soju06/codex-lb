## ADDED Requirements

### Requirement: Dashboard manages peer fallback targets

The system SHALL allow authenticated dashboard users to create, list, update, enable/disable, and delete peer fallback targets. Each target MUST have a stable identifier, normalized absolute HTTP(S) base URL, enabled flag, creation timestamp, and update timestamp.

#### Scenario: Create a peer fallback target

- **WHEN** a dashboard user creates a peer fallback target with an absolute HTTP(S) base URL
- **THEN** the system persists the normalized URL without trailing slashes
- **AND** returns the created target with a stable identifier and timestamps

#### Scenario: Reject invalid peer fallback target URL

- **WHEN** a dashboard user creates or updates a peer fallback target with a relative URL or non-HTTP(S) URL
- **THEN** the system rejects the request with a dashboard validation error
- **AND** does not persist the invalid target

#### Scenario: Toggle peer fallback target

- **WHEN** a dashboard user disables an existing peer fallback target
- **THEN** the target remains persisted
- **AND** runtime peer fallback no longer uses that target while it is disabled

### Requirement: Runtime resolves dashboard targets before environment targets

Peer fallback runtime MUST use database-registered targets when at least one target row exists, and MUST attempt only the enabled subset of those targets. When no database target rows exist, the runtime MUST preserve the existing environment-configured peer target behavior.

#### Scenario: Registered targets override environment targets

- **GIVEN** `CODEX_LB_PEER_FALLBACK_BASE_URLS` is configured
- **AND** at least one peer fallback target is registered in the database
- **WHEN** an eligible pre-output proxy failure triggers peer fallback
- **THEN** the runtime attempts the enabled database targets
- **AND** does not attempt the environment targets for that fallback decision

#### Scenario: Disabled registered targets suppress environment targets

- **GIVEN** `CODEX_LB_PEER_FALLBACK_BASE_URLS` is configured
- **AND** peer fallback targets are registered in the database
- **AND** all registered targets are disabled
- **WHEN** an eligible pre-output proxy failure triggers peer fallback
- **THEN** the runtime does not attempt any peer target
- **AND** does not attempt the environment targets for that fallback decision

#### Scenario: Environment targets remain the bootstrap default

- **GIVEN** no peer fallback target rows are registered in the database
- **AND** `CODEX_LB_PEER_FALLBACK_BASE_URLS` is configured
- **WHEN** an eligible pre-output proxy failure triggers peer fallback
- **THEN** the runtime attempts the environment-configured targets using the existing fallback behavior
