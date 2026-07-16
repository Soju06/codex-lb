## Context

The Responses retry pipeline has two related but distinct classifiers:

- `classify_upstream_failure` drives deterministic account failover decisions;
- `_should_retry_transient_stream_error` drives bounded same-account stream retry.

The existing `overloaded_error` contract is represented in the first classifier,
while `server_is_overloaded` is absent from both sets. Because an SSE terminal
event can arrive after HTTP 200, status-only classification cannot recover the
request.

## Decisions

1. Treat `server_is_overloaded` as equivalent to `overloaded_error` for failure
   classification.
2. Add both overload aliases to the existing bounded retry sets used by raw
   streaming and pre-created HTTP-bridge WebSocket requests, rather than
   introducing a new retry budget or backoff policy.
3. Preserve the current replay safety boundary: retry is allowed only before
   downstream-visible output and remains bounded by the existing request budget.
4. Cover the classifier plus both raw and HTTP-bridge routed streaming paths so
   the fix is not limited to a helper-only assertion.
5. For native Codex bridge requests only, distinguish lifecycle progress
   (`response.created` and `response.in_progress`) from actual model output. If
   either overload code terminates an accepted response before model output,
   hold the response-create admission slot, use the existing transient
   backoff, reconnect the same account, and replay the unchanged request once.
6. Preserve the retry response's real upstream ID. Codex ignores IDs on
   `response.created` and derives its next `previous_response_id` from
   `response.completed`; rewriting the successful ID to the failed attempt's
   ID would corrupt the next turn's continuity.

## Risks and Mitigations

- **Duplicate generation:** Existing stream settlement visibility checks prevent
  replay after downstream-visible output. The bridge additionally records any
  reasoning, item, or tool output before allowing accepted-response replay.
- **Unbounded retry:** The change reuses existing retry counters and request
  deadlines; it adds no new loop.
- **Unknown client errors:** Only the exact upstream overload code is added, so
  authentication and invalid-request failures remain non-retryable.
- **Public SDK lifecycle:** Accepted-response replay is disabled when the OpenAI
  SDK contract is enforced, so public streams never receive a second
  `response.created` event.
- **Continuity drift:** The retry stays on the account that accepted the parent
  response, preserves the original `previous_response_id`, and exposes the
  successful retry's actual completed ID for the following turn.

## Verification

- Unit test `classify_upstream_failure` with `http_status=None`.
- Integration test a first-event `server_is_overloaded` envelope followed by a
  successful attempt through `/backend-api/codex/responses`, with and without
  the HTTP responses session bridge.
- Integration test the production sequence `response.created`,
  `response.in_progress`, `server_is_overloaded`: assert backoff precedes a
  same-account replay, the parent anchor is unchanged, and the next request
  anchors on the retry's completed response ID.
- Unit-test fail-closed guards for public SDK streams, prior model output, and
  exhausted replay budget.
- Run Ruff, focused pytest, and strict OpenSpec validation.
