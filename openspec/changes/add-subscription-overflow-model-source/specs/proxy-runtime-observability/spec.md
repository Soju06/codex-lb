## ADDED Requirements

### Requirement: Every dispatched model-source attempt is owned and logged once

Every Responses request the proxy forwards to an OpenAI-compatible model source SHALL be owned by exactly one dispatch owner that ends in exactly one terminal disposition, and that disposition MUST write exactly one request-log row: `account_id` null, `model_source_id` and `model_source_kind` set, `api_key_id` when an API key authenticated the request, `transport` `http`, `upstream_transport` `openai_compatible_http`, `source` naming the dispatch attribution (`model_source` for direct source routing), `session_id` derived from the client's session or turn-state headers, `request_id` the source's `response.id` when one was observed (the proxy request id otherwise), `archive_request_id` the proxy request id, `requested_service_tier` the client's requested tier and `service_tier` null, usage and cost only when the source reported usage, and `status` one of `success`, `error` or `cancelled` with a stage-naming `error_code`. An abandoned dispatch is a logged attempt: a client that leaves while the source open is pending, after the response object exists but before its body started, or mid-stream MUST produce a `cancelled` row (`client_disconnected_during_open`, `source_stall_abandoned` when the source had been silent for at least 10 seconds without a frame, `client_disconnected_before_body`, `client_disconnected`, or `dispatch_interrupted` for a cancelled handler) after the source connection was closed, the reservation settled or released, and the per-source concurrency slot released. Before the API-key reservation is taken, direct source routing MUST claim a per-source concurrency slot enforcing the source's `max_concurrency` (`null` unlimited); a saturated source MUST answer HTTP `503` with error code `model_source_busy`, `error.type` `upstream_error` and `Retry-After: 1`, with no reservation and no row. Every step between the claim and the hand-over to the dispatch owner — the admission budget estimate included — MUST run under the route-helper latch, so a failure there releases the slot before the error leaves the route and a source never stays saturated by requests that owned nothing. Pre-body source failures MUST be answered with the source's own status and sanitized envelope plus the source's `Retry-After` header when it sent one. The proxy MUST export `codex_lb_model_source_dispatch_total{kind,status}`, `codex_lb_model_source_dispatch_abandoned_total{stage}` (`during_open`, `before_body`, `stall`), `codex_lb_model_source_timeout_total{phase}` (`connect`, `header`, `first_frame`, `idle`), `codex_lb_model_source_bulkhead_rejections_total{source_id}`, `codex_lb_model_source_bulkhead_in_flight{source_id}`, `codex_lb_model_source_usage_estimated_total{source_id,cause}` and `codex_lb_model_source_live_pins{kind}` (the design's `..._pins_live` renamed so the `model_source_pins` identifier stays out of `app/core` until the routing stage relaxes the inertness ratchet; registered now, sampled by the routing stage); every label set is closed or bounded by the number of configured sources. Dispatch log lines MUST carry only stage names, status codes, ids and durations: no source error message, no key material and no key fragment is emitted at INFO or WARN.

#### Scenario: Client leaves while the open is pending

- **GIVEN** a model source that delays its response headers
- **WHEN** the client disconnects while the proxy is still opening the source stream
- **THEN** the open is cancelled within one disconnect poll, the source connection is closed, the reservation is released and the concurrency slot is freed
- **AND** exactly one request-log row with status `cancelled` and error code `client_disconnected_during_open` is written
- **AND** `codex_lb_model_source_dispatch_abandoned_total{stage="during_open"}` is incremented

#### Scenario: Silent source abandoned after the evidence window

- **GIVEN** a model source that has produced no frame for at least 10 seconds after the request was sent
- **WHEN** the client disconnects
- **THEN** the row records error code `source_stall_abandoned` and the `stall` abandonment stage is counted

#### Scenario: Disconnect before the body starts

- **GIVEN** the source open completed and the streaming response object exists
- **WHEN** the client's `http.disconnect` arrives before the response body is iterated
- **THEN** the transport finalizer closes the source stream and writes one `cancelled` row with error code `client_disconnected_before_body`
- **AND** the reservation and concurrency slot are released exactly once

#### Scenario: Saturated source answers busy without owning anything

- **GIVEN** a model source with `max_concurrency` 1 and one dispatch in flight
- **WHEN** a second direct request for that source arrives
- **THEN** the response is HTTP `503` with error code `model_source_busy` and `Retry-After: 1`
- **AND** no API-key reservation and no request-log row are created for the second request
- **AND** `codex_lb_model_source_bulkhead_rejections_total` for that source is incremented

#### Scenario: Estimate failure after the claim owns nothing

- **GIVEN** a model source with `max_concurrency` 1 and a request whose body makes the admission budget estimate raise after the concurrency slot was claimed
- **WHEN** the request fails with HTTP `500`
- **THEN** the slot is released, no reservation and no request-log row exist for the attempt
- **AND** the next request for that source is dispatched instead of answering `model_source_busy`

#### Scenario: Successful dispatch row attribution

- **WHEN** a source-routed Responses stream completes with `response.completed` carrying `id` `resp_source_1`
- **THEN** the single request-log row has `source` `model_source`, `request_id` `resp_source_1`, `archive_request_id` equal to the proxy request id, `session_id` from the client's `session_id` header, `requested_service_tier` from the request and `service_tier` null
- **AND** `codex_lb_model_source_dispatch_total{kind="direct",status="success"}` is incremented

#### Scenario: Source rate limit passes through honestly

- **WHEN** the source answers HTTP `429` with `Retry-After: 7` before any body
- **THEN** the client receives HTTP `429` with the source's sanitized envelope and `Retry-After: 7`
- **AND** the reservation is released and the row records status `error` with upstream status `429`
