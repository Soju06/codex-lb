## ADDED Requirements

### Requirement: Recovery compensation retains unfinished fence ownership

Submit interruption cleanup MUST retain the rebind claim and operation metadata
until durable rollback and in-memory generation-fence rollback both succeed.
Retries MUST NOT repeat a completed durable rollback for the same claim.

#### Scenario: Fence rollback fails after durable success

- **WHEN** durable rollback succeeds and fence rollback returns false or raises
- **THEN** the claim and expected generation remain available for cleanup
- **AND** subsequent cleanup retries only the unfinished fence stage for that claim

### Requirement: String shorthand receives equivalent root recovery admission

Root recovery admission MUST accept a nonempty string input wherever it accepts
the equivalent one-user-message array, subject to the same replay safety gates.

#### Scenario: String input in a durable hard session

- **WHEN** an unanchored request contains nonempty string input
- **THEN** submit consults the durable session and arms the same eligible recovery
  anchor as the equivalent message-array request
