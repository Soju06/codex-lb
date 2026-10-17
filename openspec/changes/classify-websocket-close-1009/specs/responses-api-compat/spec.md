## ADDED Requirements

### Requirement: WebSocket close 1009 is terminal and account-neutral

When an upstream adapter exposes close code 1009 for pending Responses work,
the HTTP bridge and direct WebSocket relay MUST report payload_too_large.
This is an exact-code exception to "Upstream websocket drops penalize affected
accounts": it MUST NOT cause identical replay, account exclusion/rotation or
account error-health writes. Existing terminal cleanup MUST settle reservations,
release response-create ownership and retire the failed socket.

Before an HTTP response is committed the error MUST use HTTP 400 with
type=invalid_request_error and param=input. After commitment it MUST use the
existing terminal SSE error contract without duplicating visible output.
The direct WebSocket route MUST use its existing terminal error envelope.
Explicit request-state overrides MUST remain authoritative.

#### Scenario: Single account rejects the message size

- **WHEN** the only selected account closes 1009 before response.created
- **THEN** the client receives payload_too_large without another dispatch or a no_accounts replacement
- **AND** a following valid request can select that same account

#### Scenario: Close after output

- **WHEN** upstream closes 1009 after response creation or visible output
- **THEN** exactly one terminal size error is delivered without replaying the turn
- **AND** already-delivered output is not duplicated

#### Scenario: Other disconnects are unchanged

- **WHEN** the adapter exposes 1000, 1006, another close code, or no close code
- **THEN** this exception does not replace the existing retry and error classification
