## ADDED Requirements

### Requirement: Source-routed Responses streaming stays alive and transport-aware

Streaming source-routed `POST /v1/responses` MUST reassemble upstream byte chunks into complete SSE event blocks, MUST apply public Responses stream normalization, and MUST inject SSE comment keepalives so idle upstream gaps do not trip front-door proxy timeouts. Native Codex clients that select a model source MUST keep `codex.*` events unfiltered and MUST receive `codex.keepalive` heartbeat framing, including an initial heartbeat, matching the subscription path.

#### Scenario: Keepalive precedes a slow first upstream event

- **WHEN** a source-routed `/v1/responses` stream is open and the upstream source has not yet produced its first event within the keepalive interval
- **THEN** the client receives an SSE comment keepalive frame before the first upstream event

#### Scenario: Native Codex source stream keeps vendor framing

- **WHEN** a native Codex client streams a source-routed response
- **THEN** `codex.*` events are forwarded rather than dropped
- **AND** heartbeats use `codex.keepalive` data framing with an initial heartbeat

### Requirement: Source SSE reassembly is byte-faithful and memory-bounded

Source-routed SSE reassembly MUST recognize blank-line event separators built from any two consecutive SSE line endings (CR, LF, or CRLF, including mixed pairs), MUST preserve the original terminator bytes of events the proxy does not rewrite, MUST preserve multi-byte UTF-8 sequences and CRLF pairs split across chunk boundaries, and MUST bound the reassembled event size by the configured maximum, closing the stream when the bound is exceeded. Data blocks the proxy cannot parse MUST be forwarded byte-identically, and after raw source data has been forwarded the proxy MUST NOT synthesize a terminal event for that stream. The shared separator scan MUST locate line-ending candidates with C-level search rather than per-byte iteration in Python.

#### Scenario: Mixed line endings dispatch complete events

- **WHEN** a source terminates an SSE event with CR-only, CRLF, or mixed blank-line separators, possibly split across chunk boundaries
- **THEN** the complete event is dispatched without waiting for additional upstream data
- **AND** events the proxy does not rewrite keep their original terminator bytes

#### Scenario: Oversized source event fails closed

- **WHEN** a reassembled source SSE event exceeds the configured maximum event size
- **THEN** the stream is closed with an event-too-large failure instead of buffering without bound

#### Scenario: Unparseable source data passes through

- **WHEN** a source emits a data block that is not parseable JSON
- **THEN** the block reaches the client byte-identically
- **AND** the proxy does not append a synthesized terminal event to that stream

### Requirement: Source stream settlement and cleanup are deterministic

Source-routed Responses streaming MUST keep API-key reservation settlement outermost so an early normalization return still settles the reservation without recording a client disconnect, and MUST close the stream iterator and its owning stream deterministically on completion, failure, or cancellation, including when iterator creation or iterator close raises.

#### Scenario: Error frame still settles the reservation

- **WHEN** normalization ends a source-routed stream early on an upstream error frame
- **THEN** the API-key reservation settles
- **AND** the request is not recorded as a client disconnect

#### Scenario: Stream resources close after cancellation

- **WHEN** a source-routed stream ends by completion, failure, or client cancellation
- **THEN** the stream iterator and its owning stream are both closed
