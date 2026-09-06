## MODIFIED Requirements

### Requirement: Upstream websocket drops penalize affected accounts

The proxy MUST record a transient upstream error for the account before
signaling failure when an upstream websocket closes while one or more streamed
response requests are pending and have not reached a terminal event, except
when the close carries a classified process-wide
network failure or upstream WebSocket liveness timeout, is a clean close
(`close_code = 1000`) before any `response.*` event, carries the classified
per-socket `upstream_keepalive_timeout` transport error, or is a terminal HTTP
bridge close/error for which the adapter positively reports that no
upstream-authored close frame was observed (`close_code` is absent, the
adapter-synthesized RFC 6455 code `1006`, or an adapter-synthesized normal
closure code on EOF). A frame-less terminal HTTP bridge
ending MUST remain account-neutral regardless of response-event count or
buffered model-output progress. Unless an existing bounded pre-created recovery
succeeds, it MUST still fail the interrupted request as `stream_incomplete`.
For an error-kind message, the proxy MUST require positive adapter provenance
that the transport ended; for close-kind messages with an absent close code, it
MUST require positive adapter provenance that no close frame was received.
Protocol errors or upstream-authored empty close frames without that provenance
MUST retain their existing account-health treatment.
It MUST NOT make a post-output request eligible for transparent redispatch, and
an operation with response events or buffered model output MUST remain
acknowledged rather than recoverable. Only a frame-less drop with zero response events and no
buffered model output MAY contribute to the existing windowed eventless account
drain signal; a post-output drop MUST NOT contribute to that signal.

For other closes, the proxy MUST surface `stream_incomplete` to affected pending
requests except when a direct Responses WebSocket request has already
successfully emitted a finite integer `sequence_number`. For that sequenced
direct-WebSocket case, the proxy MUST record the request outcome as
`stream_incomplete` without emitting a synthetic terminal frame under the
active response id, then MUST close the downstream WebSocket with code 1011.

#### Scenario: websocket closes before pending responses complete

- **GIVEN** a streamed response request is pending on an upstream websocket
- **AND** the direct downstream response has not emitted a numeric sequence, or the request uses another transport
- **WHEN** the websocket closes before a terminal response event is observed
- **AND** the close is neither a classified account-neutral failure nor a frame-less terminal HTTP bridge ending
- **THEN** the pending request fails with `stream_incomplete`
- **AND** the account receives a transient upstream failure signal for routing

#### Scenario: frame-less HTTP bridge drop after output remains account-neutral

- **GIVEN** an HTTP bridge request has emitted response events or buffered model output
- **WHEN** its adapter reports a close-kind ending with no received close frame or an error-kind transport ending
- **AND** no upstream-authored close frame was received
- **THEN** the request fails once with `stream_incomplete`
- **AND** the upstream request is not transparently redispatched
- **AND** the account receives no error-health write or eventless account signal

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

#### Scenario: clean pre-response close does not penalize the account

- **GIVEN** a hard-affinity HTTP bridge request is pending with no surfaced response event
- **WHEN** the upstream websocket closes cleanly before response output
- **THEN** the proxy records the clean-close retry-circuit outcome
- **AND** the selected account is not penalized
