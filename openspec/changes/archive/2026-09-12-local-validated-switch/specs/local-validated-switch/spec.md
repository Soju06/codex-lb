## ADDED Requirements
### Requirement: Validated connection switching
The local entrance MUST retain the current destination when candidate readiness, catalog, or the configured acceptance command fails. The command MUST run with a deadline and the candidate URL. Control operations MUST be serialized and restricted to the current OS user through a private Unix socket. Successful switching MUST persist the new destination before publishing it to new connections.

#### Scenario: Failed acceptance
- **WHEN** the candidate acceptance command fails or times out
- **THEN** the previous destination remains active

### Requirement: Established connection continuity
The entrance MUST pin each accepted TCP connection to one backend and report active connection counts. Switching and rollback MUST NOT restart or stop any backend. Operators MUST NOT stop a backend until its connections have drained. Automated switching MUST NOT perform database migrations.

#### Scenario: Stream crosses switch
- **WHEN** an established stream is active while the destination switches
- **THEN** its bytes continue through the original backend and new connections use the new backend

### Requirement: Explicit disruptive maintenance
The legacy LaunchAgent stop/start CLI MUST refuse execution unless the operator explicitly enables disruptive maintenance.

#### Scenario: Default invocation
- **WHEN** the legacy CLI is invoked without the maintenance flag
- **THEN** it fails before stopping the current service
