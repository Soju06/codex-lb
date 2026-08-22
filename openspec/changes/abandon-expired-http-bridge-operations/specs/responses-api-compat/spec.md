## ADDED Requirements

### Requirement: Ambiguous HTTP bridge operations converge after owner loss

The durable HTTP bridge operation ledger MUST preserve duplicate suppression
while an operation is live or ambiguous, but an `unknown` or `acknowledged`
operation MAY transition to the terminal `abandoned` state only after its
`updated_at` is older than `max(1800 seconds,
http_responses_session_bridge_request_budget_seconds)`, no local canonical or
detached bridge request is pending for that operation, and the durable owning
session has no owner or an expired owner lease.

The transition MUST atomically compare the operation state, `updated_at`,
session owner instance, and owner epoch. A concurrent recovery claim, owner
renewal/takeover, or status proof MUST win over abandonment. The operation row
and all event history MUST remain available for normal retention. The proxy
MUST NOT automatically resend or cancel the ambiguous upstream operation.

#### Scenario: stale ownerless operation is abandoned

- **GIVEN** an operation is `unknown` or `acknowledged`
- **AND** its `updated_at` is older than the bounded inactivity cutoff
- **AND** no canonical or detached local bridge request is pending for it
- **AND** its durable session owner is absent or its lease is expired
- **WHEN** the bridge maintenance sweep runs
- **THEN** the operation becomes terminal `abandoned`
- **AND** its operation row and event history remain intact
- **AND** no upstream request is dispatched by the sweep

#### Scenario: live owner is not abandoned

- **GIVEN** an ambiguous operation is older than the inactivity cutoff
- **AND** its durable session has an unexpired owner lease
- **WHEN** the bridge maintenance sweep runs
- **THEN** the operation remains `unknown` or `acknowledged`

#### Scenario: pending local work is not abandoned

- **GIVEN** an ambiguous operation is older than the inactivity cutoff
- **AND** a canonical or detached local bridge generation still has a pending
  request state for that operation
- **WHEN** the bridge maintenance sweep runs
- **THEN** the operation remains unchanged

#### Scenario: concurrent recovery wins

- **GIVEN** a stale `unknown` operation is selected for abandonment
- **WHEN** a recovery claim changes it to `submitted` before the CAS commits
- **THEN** the abandonment affects zero rows
- **AND** the operation remains `submitted`

#### Scenario: late status proof cannot revive abandonment

- **GIVEN** an operation has become `abandoned`
- **WHEN** a late upstream event or status callback attempts to update it
- **THEN** the write is rejected or becomes a no-op
- **AND** the operation remains `abandoned`

#### Scenario: abandoned continuation requests full-history recovery

- **GIVEN** operation admission finds an existing operation in `abandoned`
- **WHEN** a client sends the same continuation again
- **THEN** the proxy does not claim, reset, or dispatch that operation
- **AND** it returns HTTP 400 with error code
  `previous_response_not_found` and parameter `previous_response_id`
- **AND** the error uses the canonical continuity contract that allows Codex
  to retry without `previous_response_id`
