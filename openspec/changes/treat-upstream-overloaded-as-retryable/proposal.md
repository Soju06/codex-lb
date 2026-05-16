## Why

Issue #565 reports that long-running Codex agent sessions stop mid-task when the upstream returns a transient `"Our servers are currently overloaded. Please try again later"` error. The codex-lb classifier in `app/modules/proxy/helpers.py:classify_upstream_failure` keys its retryable-transient decision on `_TRANSIENT_CODES = {"server_error", "upstream_error", "stream_incomplete"}` plus any 5xx HTTP status.

OpenAI surfaces the overload condition with `error.code = "overloaded_error"`. That code is missing from `_TRANSIENT_CODES`, and the error can arrive without an accompanying 5xx HTTP status (the most common path is a streamed response where the HTTP status was already `200` before the error envelope hit the wire, or a non-stream JSON response with status `503` that some upstream variants downgrade to `200` to keep the SSE channel open). In both shapes `http_status >= 500` is false, so the failover layer ends up classifying it as `non_retryable`.

`failover_decision` only retries / fails over for `failure_class in ("rate_limit", "quota", "retryable_transient")` (`app/core/balancer/logic.py:361`). With `non_retryable` the agent's request is failed back to the client and the agent stops without any account fail-over, which is exactly the symptom in #565.

## What Changes

- Add `"overloaded_error"` to `_TRANSIENT_CODES` in `app/modules/proxy/helpers.py` so the classifier returns `retryable_transient` regardless of whether the upstream surfaced an HTTP 5xx alongside the envelope.
- Document the upstream-overload classification rule under the `responses-api-compat` capability so future contributors know the failover behavior is intentional and codified.
- Add a regression test in `tests/unit/test_failover_foundation.py` covering the `code=overloaded_error`, `http_status=None` shape so accidental removal of the code from the set fails CI.

## Impact

- Restores account fail-over and retry behavior when one upstream account is overloaded, instead of stopping the agent mid-task on a transient condition that another account or a near-term retry would have served.
- No effect on `rate_limit`, `quota`, or genuinely non-retryable client errors (auth, invalid request) — the change only widens the *transient* set by one code that is, by definition, transient on the upstream side.
- No public surface change. Clients continue to see the upstream error envelope verbatim; only codex-lb's internal failover decision changes.
