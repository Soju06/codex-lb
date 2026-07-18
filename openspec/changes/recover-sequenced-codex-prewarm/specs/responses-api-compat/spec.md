## MODIFIED Requirements

### Requirement: Upstream websocket drops penalize affected accounts

When an upstream websocket closes while one or more streamed response requests
are pending and have not reached a terminal event, the proxy MUST record a
transient upstream error for the account before signaling failure for those
pending requests. The proxy MUST surface `stream_incomplete` to affected
pending requests except when a direct Responses WebSocket request has already
successfully emitted a finite integer `sequence_number`. For that sequenced
direct-WebSocket case, the proxy MUST record the request outcome as
`stream_incomplete` without emitting a synthetic terminal frame under the
active response id, then MUST close the downstream WebSocket with code 1011,
unless the request satisfies the verified no-generation prewarm recovery
contract below and its one-shot replay succeeds.

#### Scenario: websocket closes before pending responses complete

- **GIVEN** a streamed response request is pending on an upstream websocket
- **AND** the direct downstream response has not emitted a numeric sequence, or
  the request uses another transport
- **WHEN** the websocket closes before a terminal response event is observed
- **THEN** the pending request fails with `stream_incomplete`
- **AND** the account receives a transient upstream failure signal for routing

#### Scenario: ordinary sequenced direct websocket closes before completion

- **GIVEN** a direct Responses WebSocket request has successfully emitted a
  finite integer `sequence_number`
- **AND** the request does not satisfy the verified no-generation prewarm
  recovery contract
- **WHEN** the upstream websocket closes before a terminal response event is
  observed
- **THEN** the request is recorded as failed with `stream_incomplete`
- **AND** no synthetic terminal frame is emitted under the active response id
- **AND** the downstream WebSocket closes with code 1011
- **AND** the account receives a transient upstream failure signal for routing

#### Scenario: created-only generate-false Codex prewarm recovers

- **GIVEN** a direct Responses WebSocket request is classified by Codex turn
  metadata as `request_kind = "prewarm"`
- **AND** its normalized request body contains `generate = false`
- **AND** only `response.created` at numeric sequence `0` has been sent
  downstream, with no other response progress or visible output
- **WHEN** the upstream websocket closes before the terminal event
- **THEN** the proxy MAY perform the existing bounded one-shot replay
- **AND** it suppresses the replayed `response.created`
- **AND** it forwards only replay numeric sequences that advance beyond `0`
- **AND** the recovered request is finalized and logged exactly once

### Requirement: Direct WebSocket replay never mixes numeric response sequences

For direct Responses WebSocket requests, the proxy MUST NOT transparently
replay a request on a fresh upstream generation after any finite integer
`sequence_number` frame for that request has been successfully sent downstream,
except for the verified no-generation prewarm case defined below. When an
upstream close would otherwise trigger replay, the proxy MUST settle the failed
pending request without emitting frames from a new upstream generation under
the existing downstream response id, and MUST close the downstream WebSocket
with code 1011 so the client can retry on a fresh transport. When an upstream
terminal error would otherwise trigger quota, authentication, security-work,
or equivalent replay, the proxy MUST finalize and surface that terminal error
without reconnecting. Suppressed frames and non-integer sequence sentinels MUST
NOT by themselves disable otherwise-safe replay.

The sole numeric-sequence exception MUST require `request_kind = "prewarm"`, a
literal normalized `generate = false`, exactly one recorded
`response.created`, no visible output, sequence watermark `0`, a single pending
request, and the existing one-shot replay eligibility. The proxy MUST suppress
the replayed `response.created` and MUST NOT renumber or synthesize sequences.
If a later replay event has a finite integer sequence that does not advance
beyond the exposed watermark, the proxy MUST settle it as `stream_incomplete`,
emit no synthetic terminal frame, and close downstream with code 1011.

#### Scenario: Sequenced model-generating response is interrupted

- **WHEN** a direct WebSocket model-generating request has emitted
  `response.created` or another frame with a finite integer `sequence_number`
- **AND** upstream closes before a terminal response event
- **THEN** codex-lb does not transparently replay that request under the
  existing downstream response id
- **AND** no lower replay sequence is emitted downstream
- **AND** the downstream WebSocket closes with code 1011

#### Scenario: Prewarm metadata without generate-false body is not sufficient

- **WHEN** a direct WebSocket request claims `request_kind = "prewarm"`
- **BUT** its normalized body does not contain the literal `generate = false`
- **AND** a numeric sequence has been sent downstream
- **THEN** codex-lb does not transparently replay the request

#### Scenario: Progressed prewarm is not replayed

- **WHEN** a verified no-generation prewarm has emitted `response.created` and
  any additional `response.*` progress event
- **AND** upstream closes before completion
- **THEN** codex-lb does not transparently replay the request
- **AND** the downstream WebSocket closes with code 1011

#### Scenario: Replayed prewarm sequence must advance

- **GIVEN** a verified no-generation prewarm is replayed after exposing
  `response.created` at sequence `0`
- **WHEN** a non-suppressed replay frame has a finite integer
  `sequence_number <= 0`
- **THEN** codex-lb emits no frame from that replay generation downstream
- **AND** it settles the request as `stream_incomplete`
- **AND** it closes the downstream WebSocket with code 1011

#### Scenario: Unsafe replay settles request ownership

- **WHEN** sequenced replay is refused after upstream close or a replay
  sequence fails to advance
- **THEN** response-create admission, account-local leases, API-key
  reservations, and request logging are finalized exactly once
- **AND** the failed attempt does not become a successful continuity owner

#### Scenario: Sequenced retryable terminal event is not replayed

- **WHEN** a direct WebSocket request has successfully emitted a finite integer
  `sequence_number`
- **AND** upstream emits a terminal error that would ordinarily trigger
  transparent quota, authentication, or security-work replay
- **THEN** codex-lb does not reconnect or resend the request
- **AND** the terminal error is finalized and remains client-visible under the
  existing error contract

#### Scenario: Sequence-free startup remains replayable

- **WHEN** upstream closes before any numeric sequence-bearing frame has been
  successfully sent downstream
- **AND** the request otherwise satisfies the existing one-shot replay guard
- **THEN** codex-lb MAY transparently replay the request on a fresh upstream
  connection

#### Scenario: Suppressed frame does not establish exposure

- **WHEN** codex-lb suppresses an upstream frame before downstream emission
- **AND** the suppressed frame contains a numeric `sequence_number`
- **THEN** that frame does not establish the downstream sequence watermark
