# outbound-http-clients Delta

## ADDED Requirements

### Requirement: Native helper stream events are bounded per request by a byte budget

Events the native egress helper emits for one request MUST be buffered between the shared helper reader and that request's consumer in a per-request queue bounded by a queued-payload byte budget (32 MiB) together with an event-count cap (4096). Queued bytes MUST be released as the consumer drains. A burst of small framed events or a body made of large chunks that fits the byte budget MUST NOT fail a consumer that is still draining. Only when a request's queue exceeds its budget MAY the reader fail that request with `consumer_backpressure`, drop its queued events, cancel the helper-side request, and continue serving other requests.

#### Scenario: Burst of small framed events drains without failure

- **GIVEN** the helper has 2000 small body events for one request buffered in its output pipe
- **WHEN** the consumer starts reading after the burst landed
- **THEN** the consumer receives the complete body and the request is not failed

#### Scenario: A consumer that stops draining is bounded by bytes

- **GIVEN** a request whose consumer does not read while the helper emits 48 chunks of 1 MiB
- **WHEN** the queued payload exceeds 32 MiB
- **THEN** that request fails with `consumer_backpressure`
- **AND** other requests on the same helper keep being served
