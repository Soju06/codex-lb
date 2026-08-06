## ADDED Requirements

### Requirement: Responses upstream websocket liveness is bounded

The proxy MUST configure direct and routed upstream Responses WebSocket transports with finite ping/pong liveness detection derived from `proxy_downstream_websocket_idle_timeout_seconds`. When an established Responses WebSocket is terminated because its transport did not receive the required pong, the adapter MUST classify the failure as `upstream_websocket_liveness_timeout`. Direct WebSocket and HTTP bridge relay owners MUST treat that failure as account neutral, MUST NOT transparently replay a pending request whose delivery is ambiguous, MUST finalize its pending request ownership exactly once, and MUST retire the affected upstream socket so a later client retry opens a fresh connection. An HTTP bridge reader MUST suppress its own pending-deque settlement only when a concurrent submitter explicitly claimed liveness-settlement ownership under the session lifecycle lock; `session.closed` alone MUST NOT suppress settlement.

#### Scenario: Direct Responses websocket loses pong liveness

- **GIVEN** a direct upstream Responses WebSocket has been established
- **WHEN** the `websockets` keepalive watchdog terminates it after a pong timeout
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

## MODIFIED Requirements

### Requirement: Upstream websocket drops penalize affected accounts
When an upstream websocket closes while one or more streamed response requests are pending and have not reached a terminal event, the proxy MUST record a transient upstream error for the account before signaling failure for those pending requests, except when the close carries a classified process-wide network failure or upstream WebSocket liveness timeout. A classified process-wide network failure or upstream WebSocket liveness timeout MUST remain account neutral and use its classified error code. For other closes, the proxy MUST surface `stream_incomplete` to affected pending requests except when a direct Responses WebSocket request has already successfully emitted a finite integer `sequence_number`. For that sequenced direct-WebSocket case, the proxy MUST record the request outcome as `stream_incomplete` without emitting a synthetic terminal frame under the active response id, then MUST close the downstream WebSocket with code 1011.

#### Scenario: websocket closes before pending responses complete

- **GIVEN** a streamed response request is pending on an upstream websocket
- **AND** the direct downstream response has not emitted a numeric sequence, or the request uses another transport
- **WHEN** the websocket closes before a terminal response event is observed
- **AND** the close does not carry a classified process-wide network failure or upstream WebSocket liveness timeout
- **THEN** the pending request fails with `stream_incomplete`
- **AND** the account receives a transient upstream failure signal for routing

#### Scenario: sequenced direct websocket closes before completion

- **GIVEN** a direct Responses WebSocket request has successfully emitted a finite integer `sequence_number`
- **WHEN** the upstream websocket closes before a terminal response event is observed
- **AND** the close does not carry a classified process-wide network failure or upstream WebSocket liveness timeout
- **THEN** the request is recorded as failed with `stream_incomplete`
- **AND** no synthetic terminal frame is emitted under the active response id
- **AND** the downstream WebSocket closes with code 1011
- **AND** the account receives a transient upstream failure signal for routing

#### Scenario: websocket liveness timeout remains account neutral

- **GIVEN** a streamed response request is pending on an upstream websocket
- **WHEN** its transport reports `upstream_websocket_liveness_timeout`
- **THEN** the pending request fails with that classified error code
- **AND** the account receives no failure-health signal
- **AND** the request is not transparently replayed
