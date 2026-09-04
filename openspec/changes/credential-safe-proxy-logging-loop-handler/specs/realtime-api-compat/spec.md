## MODIFIED Requirements

### Requirement: Realtime forwarding preserves protocol context, privacy, and deterministic ownership

The live connector MUST replace downstream proxy authorization, account identity, and client-supplied installation identity with the bound owner identity. It MUST preserve remaining ordered query pairs and supplied version-specific alpha value or absence, FedRAMP, residency, session/context, originator, and attestation headers; strip Responses-only beta values; synthesize neither `OpenAI-Beta` nor `Sec-WebSocket-Protocol`; and apply existing egress policy. It MUST pass the exact ordered downstream WebSocket subprotocol offers through the transport negotiation API, MUST accept downstream only with an upstream-selected value that the downstream offered, and MUST preserve no selection when upstream selects none. It MUST relay text and binary messages without interpretation, preserve only bounded valid close data, enforce the existing message-size boundary, and close/cancel/await each owned peer or task at most once. Both the initial upstream close and its post-cancel drain MUST be bounded; cancellation-resistant cleanup MUST NOT delay handler completion or stream-lease release, and any eventual late task result MUST be consumed without exposing its details.

#### Scenario: protocol-faithful handshake

- **WHEN** a live caller supplies supported query and context headers
- **THEN** upstream receives those values in their required order with bound-owner credentials
- **AND** it does not receive the downstream proxy bearer, client installation identity, duplicate call id, Responses beta, or synthesized subprotocol
- **AND** any ordered subprotocol offers are negotiated upstream without a raw duplicated header
- **AND** downstream receives only the upstream-selected offered value, or no value when upstream selects none

#### Scenario: definitive denial and proxy errors remain isolated

- **WHEN** routed upstream returns a definitive handshake status
- **THEN** the proxy preserves the normalized status without route or credential details and does not replay the denial
- **WHEN** the live connector raises `InvalidProxy`, `InvalidHandshake`, or `OSError`
- **THEN** the sideband receives a fixed capability-specific, credential-safe message
- **AND** the Responses WebSocket connector returns the same fixed credential-safe message for `InvalidProxy`, logging only the connector's URL-free reason, while its `InvalidHandshake` and `OSError` behavior remains unchanged

#### Scenario: either peer disconnects or connection is cancelled

- **WHEN** a peer closes, a paired relay finishes, or the handler/connection attempt is cancelled
- **THEN** the opposite peer receives only a valid bounded close code/reason when available
- **AND** paired work is cancelled and awaited
- **AND** each peer, connector, and stream lease is released at most once
- **AND** a close task that ignores cancellation is awaited only through a fixed post-cancel drain cap before lease release continues

#### Scenario: diagnostics remain content-free

- **GIVEN** payload tracing and Responses frame archiving are enabled
- **WHEN** call creation carries SDP or the sideband carries realtime frames
- **THEN** SDP and frame bodies are absent from traces and archives
- **AND** sideband rows use `request_kind=realtime_live`, `transport=websocket`, and a redacted path
- **AND** persisted private call-creation and sideband rows omit account identity, model content, upstream error text, failure metadata, live query text, and credentials
