## ADDED Requirements

### Requirement: Abrupt eventless upstream websocket drops remain account-neutral

When an HTTP bridge upstream websocket ends with a terminal transport message (a close or receive error) that carries no upstream-authored close frame, no established account-neutral transport classification (process-network, liveness-timeout, keepalive-timeout), and no application-layer output was observed for the pending requests (zero response events and no buffered reasoning prelude), the proxy MUST NOT write per-drop account error-health (`record_error`) for that unclassified `stream_incomplete` drop. The synthetic abnormal-closure code 1006, which RFC 6455 reserves and which adapters synthesize locally when the socket dies without a close frame, MUST be treated as frame-less. When such a drop settles its pending requests as failures, the proxy MUST record it into the windowed eventless account failure signal so that repeated eventless drops on the same account within the window still apply the drain penalty; drops recovered by the bounded pre-created replay keep their existing behavior, and drops already covered by an established account-neutral transport classification keep their existing contract and are not added to the signal. A failure that carries an upstream-authored close frame (including non-clean codes), occurs after application-layer output was observed, or arrives as a non-terminal protocol-invalid frame (for example a binary message) MUST keep the existing account penalty semantics. The per-bridge retry circuit MUST still record the failure at bridge scope.

#### Scenario: Sporadic frame-less drops do not strand a continuity-bound conversation

- **GIVEN** a conversation continuity-bound to account A via `previous_response_id`
- **AND** account A's upstream websocket drops three times with no close frame and zero response events, spread wider than the eventless failure window
- **WHEN** the client sends the next continuity-bound follow-up
- **THEN** account A's `error_count` receives no per-drop increment and stays below the error-backoff threshold
- **AND** the follow-up still routes to account A instead of failing with `previous_response_owner_unavailable`

#### Scenario: Repeated eventless drops inside the window still drain the account

- **GIVEN** an account whose upstream websocket drops with no close frame and zero response events on three separate bridge failures within the eventless failure window
- **WHEN** the third drop is recorded
- **THEN** the windowed eventless failure signal applies the minimum drain penalty so new turns avoid the account until its health probe succeeds

#### Scenario: Close frames and observed-output drops keep the account penalty

- **GIVEN** an upstream websocket ending that carries an upstream-authored close frame (for example 1008 or 1011) before any response event, or a frame-less drop after application-layer output was observed (streamed response events or a buffered reasoning prelude), or a non-terminal protocol-invalid binary frame
- **WHEN** the reader failure path settles the pending requests
- **THEN** the account penalty semantics are unchanged from before this change
