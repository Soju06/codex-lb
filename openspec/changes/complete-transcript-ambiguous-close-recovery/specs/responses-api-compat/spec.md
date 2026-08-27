## ADDED Requirements

### Requirement: Eventless transport closes may recover from a durable transcript

When complete-transcript recovery is explicitly enabled and an HTTP bridge
request has a hard continuity anchor, zero upstream response events, and an
eventless transport close or timeout, the proxy MAY build a bounded,
account-neutral unanchored replay. It MUST require the durable operation fence,
same-account ownership, circuit-generation claim, and single-replay guard. If
any condition is not proven, it MUST preserve the existing fail-closed error.

#### Scenario: Old session continues after an eventless WebSocket close

- **GIVEN** the complete-transcript recovery setting is enabled
- **AND** a stale anchored continuation has no observed response events
- **AND** durable completed turns form a bounded self-contained transcript
- **WHEN** the upstream WebSocket closes before `response.completed`
- **THEN** the proxy rebinds the durable operation to an unanchored replay
- **AND** sends at most one account-pinned fresh request
- **AND** continues streaming the replacement response through the same client
  request.

### Requirement: Duplicate replay echoes remain fail-closed unless identical

The proxy MAY remove an exact duplicate tool call or tool-output echo when the
same call ID and canonical item occur more than once in a replay payload. It
MUST reject conflicting duplicate call IDs, unsettled calls, unknown fields,
and account-scoped items.

#### Scenario: Compacted client history echoes an existing tool pair

- **GIVEN** a durable replay root already contains a tool call/output pair
- **AND** the continuation request repeats the byte-equivalent pair
- **WHEN** the replay payload is built
- **THEN** one copy of each pair is retained
- **AND** fresh-replay validation still requires complete tool settlement.
