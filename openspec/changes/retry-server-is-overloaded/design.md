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
2. Add the code to the existing bounded streaming transient retry set rather
   than introducing a new retry budget or backoff policy.
3. Preserve the current replay safety boundary: retry is allowed only before
   downstream-visible output and remains bounded by the existing request budget.
4. Cover both the classifier and the public routed streaming path so the fix is
   not limited to a helper-only assertion.

## Risks and Mitigations

- **Duplicate generation:** Existing stream settlement visibility checks prevent
  replay after downstream-visible output.
- **Unbounded retry:** The change reuses existing retry counters and request
  deadlines; it adds no new loop.
- **Unknown client errors:** Only the exact upstream overload code is added, so
  authentication and invalid-request failures remain non-retryable.

## Verification

- Unit test `classify_upstream_failure` with `http_status=None`.
- Integration test a first-event `server_is_overloaded` envelope followed by a
  successful attempt through `/backend-api/codex/responses`.
- Run Ruff, focused pytest, and strict OpenSpec validation.
