# proxy-runtime-observability Delta

## ADDED Requirements

### Requirement: WebSocket scope cleanup timeout identifies its blocked phase

When WebSocket scope finalization exceeds its cleanup budget, the proxy MUST
include the current cleanup phase in the existing warning. The phase MUST be a
fixed low-cardinality value that identifies the cleanup operation and MUST NOT
contain request ids, account ids, request payloads, credentials, or exception
content. This diagnostic MUST NOT change cleanup ordering, timeout budgets,
retry behavior, or task ownership.

The phase MUST be one of `not_started`, `upstream_close`, `upstream_reader`,
`retired_create_lease`, `unsent_request`, `replay_request`, `pending_requests`,
`connection_lease`, or `complete`. `not_started` is the fallback before the
first cleanup operation begins. `complete` records finished cleanup and MUST NOT
appear in a timeout warning. Missing or unrecognized phases MUST fall back to
`not_started`; implementations MUST NOT derive a phase from request or exception
data.

#### Scenario: Pending request finalization exceeds the cleanup budget

- **GIVEN** a cancelled WebSocket scope whose pending request finalization does
  not finish within the cleanup budget
- **WHEN** the proxy emits the cleanup-budget warning
- **THEN** the warning includes `cleanup_phase=pending_requests`
- **AND** the cleanup remains owned by the existing background drain

#### Scenario: Diagnostic phase remains low-cardinality

- **WHEN** any WebSocket scope cleanup phase exceeds the cleanup budget
- **THEN** the warning identifies only a fixed cleanup phase
- **AND** the phase contains no request id, account id, payload, credential, or
  exception content
