## ADDED Requirements

### Requirement: Rust supplies payload routing metadata for interpreted WebSocket events

The native adapter MUST require `websocket_responses_routing_v1` before dispatch.
Every interpreted Responses WebSocket event MUST include `payload_response_id`
and `sequence_number`, each explicitly null when absent. Rust MUST select the
first nonempty stripped string from top-level `response_id` then nested
`response.id`, using Python whitespace and last-duplicate-key semantics.
Sequence metadata MUST accept only JSON integer tokens, excluding booleans,
floats and exponent notation, and MUST preserve precision and sign.
Objects containing any integer token over 640 digits (excluding its sign),
including nested and overwritten values, MUST retain opaque delivery so Python
integer conversion limits cannot interrupt unrelated helper exchanges.
Unsupported selected ID strings MUST retain opaque delivery without replay.

Python MUST consume native payload metadata for direct WebSocket matching,
archive attribution, bridge matching and sequence checks. A successfully
validated lifecycle event's nonempty nested response ID MUST retain its existing
unstripped precedence over payload metadata. Bridge frames requiring legacy SSE
field parsing MUST retain that parsing. Opaque frames MUST retain Python
extraction. Malformed or missing metadata MUST fail the affected exchange
without replay. Rust MUST NOT advance the downstream sequence watermark, mutate
Python pending queues, settle requests, or close shared sockets on response
terminals as part of this metadata transfer.

#### Scenario: Lifecycle validation controls response-ID precedence

- **WHEN** a lifecycle event has both a direct ID and a nested ID
- **THEN** a valid nonempty lifecycle ID wins without stripping
- **AND** failed lifecycle validation uses Rust's stripped payload ID

#### Scenario: Lossless sequence metadata reaches delivery policy

- **WHEN** an interpreted frame includes a negative or large integer sequence within the interpretation bound
- **THEN** Python receives that exact integer for replay checks
- **AND** only successful downstream delivery advances the watermark

#### Scenario: Noninteger sequence is ignored

- **WHEN** a sequence is boolean, null, float, exponent notation or a string
- **THEN** Rust emits null sequence metadata and Python does not track that value

#### Scenario: Invalid metadata does not replay an exchange

- **WHEN** an interpreted event omits routing metadata or carries invalid field types
- **THEN** the adapter fails and releases that exchange without resending the request
- **AND** other exchanges on the helper remain usable

#### Scenario: Oversized integers cannot fail the shared IPC decoder

- **WHEN** an upstream object contains an integer over 640 digits anywhere in its payload
- **THEN** the helper delivers the original text through the opaque path without embedding raw numeric metadata
- **AND** other exchanges remain usable on the same helper even if Python rejects that frame
