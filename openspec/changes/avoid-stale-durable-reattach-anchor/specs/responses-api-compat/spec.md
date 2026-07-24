## MODIFIED Requirements

### Requirement: HTTP bridge MUST support fresh upstream reattach from durable continuity

When a bridged HTTP request arrives for a valid hard continuity key but no live local session or active remote owner remains, the service MUST retain the durable owner account for routing. If the client supplies a full resend whose stored prefix matches the durable input count and fingerprint, the service MUST submit that complete payload on the fresh upstream bridge without injecting the durable `previous_response_id`. Otherwise, when the request needs the durable response anchor to represent prior context, the service MUST preserve existing durable-anchor reattach behavior. The service MUST NOT replay a request after uncertain upstream acceptance.

#### Scenario: verified full resend starts a fresh owner-bound bridge without stale anchor

- **GIVEN** a hard session's durable record contains an owner account, latest response ID, input count, and input fingerprint
- **AND** no live local bridge or active remote owner remains
- **WHEN** the client sends a full resend whose stored prefix matches both the durable count and fingerprint
- **THEN** the service opens the fresh bridge on the durable owner account
- **AND** it submits the complete client input without `previous_response_id`
- **AND** it does not trim the verified prefix from that submission

#### Scenario: incremental reattach still uses durable anchor

- **GIVEN** a hard session's durable record contains an owner account and latest response ID
- **AND** no live local bridge or active remote owner remains
- **WHEN** the client sends an incremental follow-up that is not a verified full resend
- **THEN** the service injects the durable latest response ID as the reattach anchor
- **AND** it remains on the durable owner account

#### Scenario: eventless accepted request is not replayed

- **GIVEN** a bridge request has already been sent upstream
- **WHEN** the upstream emits no response lifecycle event before the eventless watchdog expires
- **THEN** the service fails and retires the bridge session through the existing fail-closed path
- **AND** it does not replay the ambiguously accepted request
