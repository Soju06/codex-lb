## Why

An HTTP responses bridge session allows one in-flight `response.create` per upstream WebSocket session (`response_create_gate = Semaphore(1)`), and the bridge is designed for queued same-session work: the per-session queue admits up to `http_responses_session_bridge_queue_limit` (default 8) waiters and the bridge request budget is `http_responses_session_bridge_request_budget_seconds` (default 7200s). But the gate wait itself reuses the global `proxy_admission_wait_timeout_seconds` (default 10s), and `response_create_gate_timeout` is not a recoverable capacity-wait code, so a queued request that cannot soft-reroute — any hard-affinity key (`session_header`, `turn_state_header`) or any request carrying `previous_response_id` — fails with HTTP 429 "codex-lb is temporarily overloaded" after only 10 seconds whenever the current turn runs longer than that. Operators observe this as concurrency failures while the account pool is idle and upstream rate limits are untouched.

## What Changes

- Treat per-session response-create gate contention as a recoverable capacity wait for bridged Responses requests: after a bounded gate acquisition attempt times out, the request re-queues with capacity-wait keepalives and retries, bounded by the bridge request budget, instead of failing terminally at the first attempt.
- Keep each individual gate acquisition attempt bounded by `proxy_admission_wait_timeout_seconds` so stuck-session detection and retirement keep running between attempts.
- Keep the existing precedence: soft-affinity requests without `previous_response_id` still reroute to a fresh session first; `bridge_queue_full` remains a fail-fast local overload; budget exhaustion still surfaces the terminal local overload error.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `proxy-admission-control`: per-session response-create gate contention becomes a recoverable, budget-bounded wait for bridged Responses requests instead of a terminal 429 at the first admission timeout.

## Impact

- Code: `app/modules/proxy/_service/http_bridge/streaming.py`
- Tests: `tests/unit/test_proxy_http_bridge.py`, `tests/integration/test_http_responses_bridge.py`
- Specs: `openspec/specs/proxy-admission-control/spec.md`
