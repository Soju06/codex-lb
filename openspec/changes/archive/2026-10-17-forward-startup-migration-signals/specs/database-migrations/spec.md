## ADDED Requirements

### Requirement: Container startup migrations own termination signals

While a shipped container entrypoint is running a startup migration, its supervisor MUST own SIGTERM and SIGINT, forward each received signal to the active migration child, and reap that child before the supervisor exits or hands off to the application. The first signal received by the supervisor MUST determine a normalized `128 + signal` exit status, and an interrupted startup MUST NOT launch the application even if the migration child later reports success.

A migration that exits without parent interruption MUST preserve its status: a positive nonzero status MUST be returned unchanged, a signal termination MUST be normalized to `128 + signal`, and only status zero MAY continue to application startup.

#### Scenario: SIGTERM interrupts startup migration

- **WHEN** a shipped container entrypoint receives SIGTERM while its startup-migration child is active
- **THEN** the same signal is delivered to the migration child
- **AND** the child is reaped before the supervisor exits
- **AND** the supervisor exits with status `143`
- **AND** the application is not launched

#### Scenario: SIGINT interrupts startup migration

- **WHEN** a shipped container entrypoint receives SIGINT while its startup-migration child is active
- **THEN** the same signal is delivered to the migration child
- **AND** the child is reaped before the supervisor exits
- **AND** the supervisor exits with status `130`
- **AND** the application is not launched

#### Scenario: Startup migration succeeds

- **WHEN** the startup-migration child exits with status zero and the supervisor received no termination signal
- **THEN** the child is reaped
- **AND** the application launch continues exactly once

#### Scenario: Startup migration fails

- **WHEN** the startup-migration child exits nonzero and the supervisor received no termination signal
- **THEN** the child is reaped
- **AND** the supervisor returns the child's positive status unchanged or normalizes its terminating signal to `128 + signal`
- **AND** the application is not launched
