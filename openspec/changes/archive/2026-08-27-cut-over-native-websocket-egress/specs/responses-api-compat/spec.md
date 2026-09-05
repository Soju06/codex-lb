## ADDED Requirements

### Requirement: Responses WebSocket preserves bidirectional transport semantics

The Responses WebSocket relay MUST preserve ordered text and binary messages, selected subprotocol response metadata, close codes, and terminal error delivery across its downstream and upstream boundaries. A direct upstream connection MUST use native Codex-family WebSocket egress when the fixed helper is available before dispatch, while an account-routed connection MUST retain route-aware setup and cleanup. Ping and pong control frames MUST remain transport-owned and MUST NOT surface as application events. A frame whose native send acknowledgement is ambiguous or failed MUST NOT be replayed.

#### Scenario: Native direct relay preserves frames

- **GIVEN** a direct Responses WebSocket uses the native helper
- **WHEN** text and binary frames travel in both directions
- **THEN** their type, payload, and ordering are preserved
- **AND** control ping and pong frames are handled below the application relay

#### Scenario: Native direct relay preserves terminal close

- **WHEN** the native upstream sends a close frame
- **THEN** the relay observes its close code and reason
- **AND** the native connection is removed from the helper's active registry

#### Scenario: Ambiguous native frame send fails closed

- **GIVEN** a downstream `response.create` frame is dispatched to the helper
- **WHEN** acknowledgement fails because the helper or connection closes
- **THEN** the turn surfaces a terminal transport failure
- **AND** the frame is not resent on another transport

## MODIFIED Requirements

### Requirement: Responses upstream websocket liveness is bounded

The proxy MUST configure direct and routed upstream Responses WebSocket transports with finite ping/pong liveness detection derived from `proxy_downstream_websocket_idle_timeout_seconds`. A direct connection MUST use the native helper watchdog when native egress is selected and the Python `websockets` watchdog only on the pre-dispatch missing-helper fallback. When an established Responses WebSocket is terminated because its transport did not receive the required pong, the adapter MUST classify the failure as `upstream_websocket_liveness_timeout`. Direct WebSocket and HTTP bridge relay owners MUST treat that failure as account neutral, MUST NOT transparently replay a pending request whose delivery is ambiguous, MUST finalize its pending request ownership exactly once, and MUST retire the affected upstream socket so a later client retry opens a fresh connection. An HTTP bridge reader MUST suppress its own pending-deque settlement only when a concurrent submitter explicitly claimed liveness-settlement ownership under the session lifecycle lock; `session.closed` alone MUST NOT suppress settlement.

#### Scenario: Direct Responses websocket loses pong liveness

- **GIVEN** a direct upstream Responses WebSocket has been established
- **WHEN** the selected native-helper or Python fallback keepalive watchdog terminates it after a pong timeout
- **THEN** the pending request fails with `upstream_websocket_liveness_timeout`
- **AND** the request is not transparently replayed
- **AND** the selected account receives no failure-health signal
- **AND** the affected upstream socket is retired

#### Scenario: Routed Responses websocket loses pong liveness

- **GIVEN** a routed upstream Responses WebSocket has been established for an HTTP bridge or direct WebSocket client
- **WHEN** the aiohttp heartbeat watchdog terminates it after a pong timeout
- **THEN** the pending request fails with `upstream_websocket_liveness_timeout`
- **AND** the request is not transparently replayed
- **AND** the selected account receives no failure-health signal
- **AND** the affected upstream socket is retired

#### Scenario: Long turn remains healthy through control frames

- **GIVEN** a Responses turn emits no application event within the liveness interval
- **WHEN** the upstream WebSocket continues replying to transport pings
- **THEN** the proxy keeps the upstream socket open
- **AND** the existing Responses request budget remains authoritative for the turn

#### Scenario: Closed bridge without a sender claim later loses pong liveness

- **GIVEN** an HTTP bridge session has multiple pending requests
- **AND** a separate submit failure marks the session closed without claiming liveness-settlement ownership
- **WHEN** the still-running upstream transport later expires its heartbeat
- **THEN** the reader settles every pending request with `upstream_websocket_liveness_timeout`
- **AND** the selected account receives no failure-health signal

#### Scenario: Claimed bridge settlement survives submitter cancellation

- **GIVEN** an HTTP bridge submitter claims liveness-settlement ownership after its send fails
- **WHEN** the submitter is cancelled before whole-deque settlement completes
- **THEN** settlement continues until every pending sibling is finalized exactly once
- **AND** the submitter cancellation is preserved after settlement completes
