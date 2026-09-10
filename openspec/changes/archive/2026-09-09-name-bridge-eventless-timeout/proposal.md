## Why

The HTTP responses bridge kills a client stream that produced no response
events before `response.created` and reports the kill as `stream_idle_timeout`.
That code is a lie in two directions.

- The budget it names, `stream_idle_timeout_seconds` (7200s at shipped
  defaults), governs gaps *after* a response starts. The pre-response kill
  actually fired at the implicit product
  `_STREAM_KEEPALIVE_MAX_COUNT (6) * sse_keepalive_interval_seconds (10s)`, i.e.
  ~60s, which is not derived from any configured timeout at all.
- It blames the upstream for what is usually a local bridge handoff wedge.
  In a 48h window, successful `gpt-5.6-luna` turns had a p95 first-upstream-event
  latency of 930ms and `0/3063` above 60s, while the incident logs were dominated
  by zero-event failures. The pre-response watchdog was racing (and beating) the
  owner-side `missing_response_created_timeout` gate, so the honest, specific
  classification lost to the misleading generic one.

Four local bridge recovery paths compounded this by settling their own resets
with the message `Upstream websocket closed before response.completed` even
though no upstream close happened.

## What Changes

- Pre-response-start bridge failures emit a distinct `bridge_eventless_timeout`
  classification in logs, request-log `failure_detail` / `failure_phase`,
  retry-circuit `last_detail`, and the client-visible error. `stream_idle_timeout`
  is now reserved for post-`response.created` silence.
- The client-visible shape stays retryable: a `503` with a message that states
  no response was created upstream and the request is safe to retry, instead of
  a `502` blaming the upstream keepalive window.
- The pre-response budget becomes a named, settings-derived quantity
  (`_http_bridge_eventless_budget_seconds` =
  `min(stuck_gate_retire_after_seconds, stream_idle_timeout_seconds,
  bridge_request_budget_seconds)`), aligning it with the 300s owner-side stuck
  gate instead of the implicit 6 x 10s product.
- Upstream text frames that prove transport liveness but match no pending
  request now emit an explicit `unmatched_upstream_liveness` bridge-event marker
  and a per-session counter, which the eventless timeout reports. Locally
  injected `codex.keepalive` frames are excluded.
- The four local bridge reset sites say local bridge reset. `Upstream websocket
  closed ...` is reserved for actual upstream closes.

## Impact

- Affected capabilities: `responses-api-compat`.
- New error code `bridge_eventless_timeout` is client-visible on the responses
  surface. It is a new, narrower name for failures that previously surfaced as
  `stream_idle_timeout` / `upstream_request_timeout`; no previously-successful
  request changes shape.
- Pre-response silence now tolerates up to the stuck-gate budget (300s at
  defaults) rather than ~60s. The owner-side `missing_response_created_timeout`
  watchdog (<= 60s) remains the first responder for requests whose
  `response.create` was actually sent, so the common recovery path keeps its
  latency; the downstream watchdog becomes an honest backstop instead of a
  racing mislabeler.
- No new settings, no migration, no live deployment or database mutation.

## Follow-ups (not in this change)

- `app/core/timeout_invariants.py` does not exist on this branch lineage, so the
  requested lint rules are encoded as a unit test
  (`test_http_bridge_eventless_budget_is_named_and_settings_derived`) rather than
  a linter module. When that module lands, move these invariants into it:
  `eventless_budget <= stuck_gate_retire_after_seconds`,
  `eventless_budget <= stream_idle_timeout_seconds`,
  `eventless_budget <= http_responses_session_bridge_request_budget_seconds`,
  `eventless_budget >= sse_keepalive_interval_seconds`, and
  `pre_response_keepalive_count * sse_keepalive_interval_seconds >= eventless_budget`.
- Raising `_STREAM_KEEPALIVE_MAX_COUNT` itself (ranked fix 5 in the audit) was
  deliberately not taken: it hides the symptom without fixing the semantic
  inversion, and with a named settings-derived budget the constant is only a
  floor on keepalive cadence, not a deadline.
