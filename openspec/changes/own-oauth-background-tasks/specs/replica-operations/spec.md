## ADDED Requirements

### Requirement: Detached OAuth callback cleanup remains owned until done
The OAuth state store MUST retain each idle callback-listener cleanup task spawned after a callback until that task is done. Successful, failed and cancelled tasks MUST release the retained reference. Unexpected failure MUST be observed with a diagnostic that excludes exception text, credentials and flow identifiers. Callback responses and existing idle-listener stop serialization MUST remain unchanged.

#### Scenario: Callback cleanup is pending
- **WHEN** a callback spawns idle-listener cleanup that cannot yet finish
- **THEN** the state store retains that cleanup task without waiting before returning the callback response

#### Scenario: Callback cleanup finishes
- **WHEN** a retained cleanup task succeeds, fails or is cancelled
- **THEN** the state store releases its reference after completion
- **AND** a failure is retrieved without leaking credentials in diagnostics
