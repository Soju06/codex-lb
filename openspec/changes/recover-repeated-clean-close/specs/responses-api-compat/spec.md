## MODIFIED Requirements

### Requirement: HTTP bridge clean-close replay remains bounded and pre-visible

When the HTTP Responses bridge observes an upstream WebSocket close with
`close_code = 1000` before any `response.*` event has been surfaced for the
pending request, the proxy MUST preserve its existing pre-visible replay
guards. If the request has already used exactly one eligible pre-visible
replay and the replacement upstream WebSocket also closes cleanly before any
response event, the proxy MAY perform exactly one additional replay. The
additional replay MUST be hard-capped at one per request, and the configured
maximum MUST NOT raise that cap.

The proxy MUST NOT replay after downstream-visible output, after a terminal
response event, or when continuity-sensitive request state makes replay unsafe.
Before the additional replay, the proxy MAY sleep for bounded configured
jitter. The proxy MUST emit a dedicated low-cardinality diagnostic event for
the additional replay.

When a downstream HTTP stream task initiates pre-response recovery while the
upstream reader is blocked on the superseded socket, the proxy MUST cancel and
await that reader before locally closing the socket. It MUST then start exactly
one reader for the replacement socket. A close caused by replacing the socket
MUST NOT be recorded as an upstream clean-close failure, MUST NOT increment the
retry circuit, and MUST NOT retire pending work moved to the replacement. The
cancelled reader's socket-generation finalizer MUST NOT leave the shared session
marked closed while the replacement socket is being selected or opened, so idle
pruning MUST NOT evict the handoff in progress.

The default pre-response idle-recovery window MUST leave bounded headroom
before the downstream client's request timeout. With the default ten-second
keepalive interval, the proxy MUST initiate eligible recovery after no more
than six silent intervals so replacement connection and first output can occur
before a 120-second client deadline.

#### Scenario: clean close before response output receives one bounded additional replay

- **GIVEN** an HTTP bridge request has no surfaced `response.*` events
- **AND** its first pre-visible replay has already been used
- **WHEN** the replacement upstream WebSocket closes with code `1000`
- **THEN** the proxy performs one additional pre-visible replay
- **AND** the request replay count increases by one
- **AND** the proxy emits a `retry_precreated_clean_close` diagnostic event

#### Scenario: repeated clean closes do not create an unbounded replay loop

- **GIVEN** the additional clean-close replay has already been used
- **WHEN** another upstream WebSocket closes cleanly before response output
- **THEN** the proxy does not replay the request again
- **AND** the existing terminal or circuit handling is used

#### Scenario: visible output still prevents clean-close replay

- **GIVEN** the pending request has surfaced any response event downstream
- **WHEN** the upstream WebSocket closes with code `1000`
- **THEN** the proxy does not replay the request

#### Scenario: clean-close retry jitter is bounded

- **GIVEN** clean-close retry jitter is configured
- **WHEN** the additional clean-close replay is scheduled
- **THEN** the delay is no greater than the configured jitter maximum
- **AND** the hard replay cap remains one regardless of the configured value

#### Scenario: downstream idle recovery transfers reader ownership

- **GIVEN** the upstream reader is blocked on the current bridge socket
- **AND** the downstream HTTP stream task initiates eligible pre-response recovery
- **WHEN** the bridge replaces the upstream socket
- **THEN** the old reader is cancelled and awaited before its socket is closed
- **AND** the shared session remains live while the replacement socket opens
- **AND** exactly one reader owns the replacement socket
- **AND** the local close does not open or increment the retry circuit
- **AND** pending work remains attached to the replacement session

#### Scenario: silent pre-response recovery precedes the client timeout

- **GIVEN** the upstream has produced no response event
- **AND** the default ten-second keepalive interval is active
- **WHEN** six silent intervals elapse
- **THEN** the proxy initiates eligible pre-response recovery
- **AND** at least sixty seconds remain before a 120-second client request timeout
