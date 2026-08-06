## ADDED Requirements

### Requirement: Ownerless durable bridge rows remain recoverable

When a durable HTTP bridge row has no `owner_instance_id`, a replacement MUST
be allowed to claim it even if the row is still marked active, and the claim
MUST advance `owner_epoch` so late cleanup from the previous session is fenced.
A row actively owned by another instance MUST retain the existing
`bridge_instance_mismatch` behavior.

#### Scenario: replacement after a clean upstream close

- **WHEN** the previous bridge session closes cleanly and its durable release races replacement creation
- **AND** the durable row is observed without an owner
- **THEN** the replacement claims the row with a higher owner epoch
- **AND** the request reconnects successfully

#### Scenario: previous-response recovery after a clean close

- **WHEN** a follow-up uses `previous_response_id` after the prior bridge socket closed cleanly
- **AND** the durable row is ownerless during replacement
- **THEN** the service opens a fresh local session and completes the follow-up
