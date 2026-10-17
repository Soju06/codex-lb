## ADDED Requirements

### Requirement: Routed native Responses streams consume Rust-framed SSE

Account-routed streaming Responses HTTP requests using native egress MUST
delegate byte framing, event byte limits, and body-read idle deadlines to the
existing Rust SSE transport contract. Python MUST consume the framed events
without byte reframing, preserving normalization, terminal detection, rate-limit
headers, archives, route trace, and public error envelopes. Non-streaming HTTP
responses and HTTP errors MUST retain raw body consumption. Body failures and
cancellation MUST NOT replay a dispatched POST or switch its proxy endpoint.

#### Scenario: Routed native success skips Python byte framing

- **WHEN** the selected proxy endpoint returns a successful streaming Responses body
- **THEN** Rust emits the same SSE event contract as direct native streaming
- **AND** Python performs ordinary downstream event processing without scanning bytes

#### Scenario: Framing failure preserves route and error behavior

- **WHEN** a routed body exceeds its byte limit or goes idle
- **THEN** the existing stream-event-too-large or stream-idle-timeout envelope is produced
- **AND** route metadata remains associated with that attempt without endpoint replay

#### Scenario: Routed cancellation isolates the owned stream

- **WHEN** a routed native SSE stream closes or is cancelled, including in an already cancelled scope
- **THEN** its owned native request is cancelled and unregistered
- **AND** a locally created routed client finishes closing its session before cancellation propagates
- **AND** another active request in the same helper remains usable

#### Scenario: Routed error or non-streaming response remains raw

- **WHEN** the routed response is an HTTP error or the request disables streaming
- **THEN** the ordinary JSON/error body path and public response envelope are preserved
