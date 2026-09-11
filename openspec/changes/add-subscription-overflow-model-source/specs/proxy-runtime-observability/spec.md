## ADDED Requirements

### Requirement: Every dispatched model-source attempt is owned and logged once

Every Responses request the proxy forwards to an OpenAI-compatible model source SHALL be owned by exactly one dispatch owner that ends in exactly one terminal disposition, and that disposition MUST write exactly one request-log row: `account_id` null, `model_source_id` and `model_source_kind` set, `api_key_id` when an API key authenticated the request, `transport` `http`, `upstream_transport` `openai_compatible_http`, `source` naming the dispatch attribution (`model_source` for direct source routing), `session_id` derived from the client's session or turn-state headers, `request_id` the source's `response.id` when one was observed (the proxy request id otherwise), `archive_request_id` the proxy request id, `requested_service_tier` the client's requested tier and `service_tier` null, usage and cost only when the source reported usage, and `status` one of `success`, `error` or `cancelled` with a stage-naming `error_code`. An abandoned dispatch is a logged attempt: a client that leaves while the source open is pending, after the response object exists but before its body started, or mid-stream MUST produce a `cancelled` row (`client_disconnected_during_open`, `source_stall_abandoned` when the source had been silent for at least 10 seconds without a frame, `client_disconnected_before_body`, `client_disconnected`, or `dispatch_interrupted` for a cancelled handler) after the source connection was closed, the reservation settled or released, and the per-source concurrency slot released. Before the API-key reservation is taken, direct source routing MUST claim a per-source concurrency slot enforcing the source's `max_concurrency` (`null` unlimited); a saturated source MUST answer HTTP `503` with error code `model_source_busy`, `error.type` `upstream_error` and `Retry-After: 1`, with no reservation and no row. Every step between the claim and the hand-over to the dispatch owner — the admission budget estimate included — MUST run under the route-helper latch, so a failure there releases the slot before the error leaves the route and a source never stays saturated by requests that owned nothing. Pre-body source failures MUST be answered with the source's own status and sanitized envelope plus the source's `Retry-After` header when it sent one. The proxy MUST export `codex_lb_model_source_dispatch_total{kind,status}`, `codex_lb_model_source_dispatch_abandoned_total{stage}` (`during_open`, `before_body`, `stall`), `codex_lb_model_source_timeout_total{phase}` (`connect`, `header`, `first_frame`, `idle`), `codex_lb_model_source_bulkhead_rejections_total{source_id}`, `codex_lb_model_source_bulkhead_in_flight{source_id}`, `codex_lb_model_source_usage_estimated_total{source_id,cause}` and `codex_lb_model_source_live_pins{kind}` (the design's `..._pins_live` renamed so the `model_source_pins` identifier stayed out of `app/core` under the inertness ratchet, and kept after the ratchet became positive; sampled once per hourly retention tick by the leader replica right after the pin prune, one value per pin kind -- no dedicated task) and `codex_lb_model_source_breaker_state{source_id}` (`0` closed, `1` open, `2` half-open; the overflow breaker, set on every transition); every label set is closed or bounded by the number of configured sources. The `kind` label of `codex_lb_model_source_dispatch_total` MUST be `direct` for direct source routing and `fresh`, `pinned` or `anchor` for subscription-overflow dispatches; the design's separate overflow result counter is folded into this counter. The routing stage MUST export `codex_lb_subscription_overflow_total{route,outcome}` with the closed `route` set `codex_responses`, `v1_responses`, `websocket_handshake`, `websocket`, `compact` and the closed `outcome` set `dispatched_fresh`, `dispatched_pinned`, `dispatched_anchor`, `bounced_ws_handshake`, `bounced_ws_event`, `declined_pin_commit_recent_failure`, `declined_turn_state_bound`, `declined_opportunistic`, `declined_key_scope`, `declined_no_thread_key`, `declined_background_job`, `declined_source_excluded`, `declined_breaker_open`, `declined_drain_mode`, `declined_no_source`, `declined_model_unlisted`, `declined_not_portable_history`, `declined_not_portable_input`, `declined_source_busy`, `pinned_unservable_source_disabled`, `pinned_unservable_source_deleted`, `pinned_unservable_model_unlisted`, `pinned_unservable_tombstone`, `pinned_unsupported_input`, `pinned_breaker_open`, `pinned_busy`, `pinned_released_neutral`, `pinned_release_failed`, `pinned_lookup_timeout`, `pin_commit_failed`, `pin_commit_unverified`, `decision_error` -- exactly the values the decision module defines, so a name added on either side is a test failure; decline details (tool and item types, field names, source names) go to the WARN line, never to a label. Every finished overflow dispatch MUST also record one `codex_lb_upstream_transport_decisions_total{policy="subscription_overflow"}` increment with `sticky` `false` for a fresh dispatch and `true` for pinned and anchored ones. Overflow log lines MUST use these names: INFO `subscription_overflow_dispatched` (kind, source id, model, route, request id); DEBUG declines and a rate-limited WARN (once per source and reason per 60 s) for configuration-class declines with the offending type or field; WARN `subscription_overflow_pinned_unservable cause=<cause>`, `subscription_overflow_pinned_released_neutral`, `model_source_breaker state=<state> source_id=<id>`, `subscription_overflow_decision_error stage=<stage>`, `model_source_pin_write outcome=<outcome>`; and an in-band WebSocket bounce whose bounce row was not written logs the outcome at WARN and continues. Request-log rows carry `source` `subscription_overflow` or `subscription_overflow_pinned` for overflow attempts; a source name or id never reaches a client envelope. Dispatch log lines MUST carry only stage names, status codes, ids and durations: no source error message, no key material and no key fragment is emitted at INFO or WARN.

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

#### Scenario: Overflow counter labels are closed

- **WHEN** a fresh dispatch, a pinned dispatch, an anchored dispatch, a handshake denial, an in-band bounce and a decline happen
- **THEN** `codex_lb_subscription_overflow_total` is incremented once each with `route` in `codex_responses`, `v1_responses`, `websocket_handshake`, `websocket`, `compact` and `outcome` in the closed set
- **AND** `codex_lb_model_source_dispatch_total` is incremented with `kind` `fresh`, `pinned` or `anchor` and `codex_lb_upstream_transport_decisions_total{policy="subscription_overflow"}` once per finished dispatch
- **AND** no tool type, field name or source name appears in a label

#### Scenario: Breaker and live-pin gauges

- **WHEN** the overflow breaker for a source opens, half-opens and closes
- **THEN** `codex_lb_model_source_breaker_state{source_id}` reads `1`, `2` and `0` in turn and each transition logs `model_source_breaker state=<state>`
- **WHEN** the leader replica's hourly retention tick runs
- **THEN** `codex_lb_model_source_live_pins{kind}` is set to the live row count per pin kind after the prune, and no other task samples it
