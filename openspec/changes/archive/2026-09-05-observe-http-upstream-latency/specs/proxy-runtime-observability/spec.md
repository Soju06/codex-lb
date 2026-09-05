## MODIFIED Requirements

### Requirement: TTFT phase timings are persisted and exported
The proxy MUST persist nullable low-cardinality request-log fields for TTFT phase analysis and MUST export equivalent Prometheus phase latency observations without labels containing raw API keys, raw session ids, raw affinity keys, request ids, or prompt text.

#### Scenario: HTTP bridge request records phase timing
- **WHEN** a visible HTTP bridge request waits for session response-create admission and then receives upstream `response.created`
- **THEN** the request log includes integer millisecond timing for response-create gate wait and upstream response-created latency
- **AND** Prometheus observes phase latency with only stable labels such as phase, transport, upstream transport, and model class

#### Scenario: First upstream event is distinct from first token
- **WHEN** the upstream bridge reader receives an upstream event before text delta output
- **THEN** the request log can record first upstream event latency separately from first downstream token latency

HTTP Responses attempts MUST also populate the existing nullable `latency_first_upstream_event_ms` and `latency_response_created_ms` fields from actual upstream observations, measured from the same current attempt anchor as `latency_first_token_ms` and `latency_ms`. Pre-attempt selection/admission time MUST remain in `latency_queue_ms`. The first upstream data event and first `response.created` MUST be measured independently from first token-bearing output. Unobserved phases MUST remain null; legitimate zero-millisecond observations MUST be retained. Local heartbeat/comment/sentinel output MUST NOT create an upstream-event timestamp. Capturing these values MUST preserve existing lazy/verbatim SSE forwarding and the existing persistence/export owner.

#### Scenario: HTTP created precedes content
- **WHEN** an HTTP attempt observes an upstream data event, `response.created`, and first token-bearing output at distinct controlled times
- **THEN** the persisted first-event, created and first-token fields retain those distinct offsets from the existing attempt anchor
- **AND** existing queue and total latency semantics are unchanged

#### Scenario: Missing and immediate HTTP phases remain distinguishable
- **WHEN** an HTTP attempt observes an event at its timing origin but never observes `response.created`
- **THEN** first-upstream-event latency is zero and response-created latency is null
- **AND** no terminal event or TTFT fallback invents a created timestamp

#### Scenario: HTTP timing preserves the token fast path
- **WHEN** later SSE token events qualify for existing verbatim relay
- **THEN** phase timing does not force their parsing or re-serialization
- **AND** the downstream event bytes and existing TTFT classification remain unchanged
