# Handover: HTTP Bridge Keepalive Hang on Invalid Model

## Reported Bug

curl -vv -skL http://127.0.0.1:2455/backend-api/codex/responses \
  -H "Content-Type: application/json" \
  -d '{"model": "bogus", "input": [{"role": "user", "content": "Which model are you?"}], "instructions": "You are a informative model. Answer short but technical.","stream": false, "store": false, "tools": []}'

Sending an unsupported model name causes codex-lb to send `: keepalive` forever instead of returning a terminal error event. First invocation sometimes returns `response.failed` correctly, but subsequent invocations hang indefinitely.

**Symptoms:**
- Client sees `: keepalive` every ~10s with no `event: response.failed` or `data: [DONE]`
- Stream terminates only after the 600s `proxy_request_budget_seconds` timeout
- The prewarm lock (`response_create_gate`) may be held, preventing future requests on the same bridge session

## Root Cause Analysis

Two independent bugs were found:

### 1. `_maybe_prewarm_http_bridge_session` blocks forever (primary)

`app/modules/proxy/service.py:_maybe_prewarm_http_bridge_session` at line ~6188 sends a warmup request upstream with the same model, then enters a tight loop:

```python
while True:
    event_block = await event_queue.get()  # ← NO TIMEOUT
```

- If the upstream WebSocket is broken (half-open TCP, upstream dropped the connection after a previous error, upstream rate-limiting connections, etc.), `event_queue.get()` **never returns**.
- The prewarm holds the `response_create_gate` semaphore (line ~6179-6181), so the **actual request can never be sent**. The caller task is stuck at `_submit_http_bridge_request` → `_maybe_prewarm_http_bridge_session` and never reaches the keepalive loop in `_stream_http_bridge_session_events`.
- The only keepalive the client sees comes from the outer `inject_sse_keepalives` wrapper (also every 10s), masking the fact that the bridge loop never started.

**Why intermittent?** The first request creates a fresh bridge session with a healthy WebSocket, so the upstream responds quickly and the prewarm succeeds or fails fast. The session then stays in cache with `session.closed = False`. If the upstream subsequently closes the connection (or if a `_fail_pending_websocket_requests` path doesn't properly set `session.closed = True`), the second request reuses the stale session and the prewarm blocks forever.

### 2. `_stream_http_bridge_session_events` keepalive loop has no backstop

Even when the prewarm succeeds and the actual request is sent, if the upstream silently stops responding (half-open TCP, upstream crash, upstream filtering the request), the keepalive loop emits `: keepalive\n\n` indefinitely:

```python
except asyncio.TimeoutError:
    yield ": keepalive\n\n"
    continue  # ← forever
```

The only bound is `proxy_request_budget_seconds` (default 600s) enforced by the upstream reader task's `_next_websocket_receive_timeout`. This leaves the client hanging for up to 10 minutes.

## Fix

File: `app/modules/proxy/service.py`

### Change 1: Prewarm timeout (line ~6188)

Added `_PREWARM_RESPONSE_TIMEOUT_SECONDS = 2.0` and wrapped the prewarm's `event_queue.get()` with `asyncio.wait_for(..., timeout=...)`.

On timeout:
- Logs `HTTP bridge prewarm timed out`
- Sets `session.prewarmed = False`
- Calls `_cleanup_http_bridge_submit_interruption` to release the gate and remove the warmup state from `pending_requests`
- Returns **without raising** → the actual request continues normally without prewarming

**Key design decision:** The prewarm is an optimization. If the upstream is unresponsive during warmup, we should not let that block the actual request. The actual request will either succeed (if the upstream recovers) or fail with the proper upstream error — both are strictly better than hanging.

### Change 2: Keepalive count limit (line ~1469)

Added `_STREAM_KEEPALIVE_MAX_COUNT = 6` (≈60s at default 10s interval) to the `_stream_http_bridge_session_events` keepalive loop:

- Tracks consecutive keepalives via `keepalive_count`
- Resets `keepalive_count = 0` when a real event arrives (line ~1509)
- When `keepalive_count > _STREAM_KEEPALIVE_MAX_COUNT`: yields a `response.failed` event with code `stream_idle_timeout` and breaks the loop

## New Constants

```python
# app/modules/proxy/service.py (~line 218)
_PREWARM_RESPONSE_TIMEOUT_SECONDS = 2.0
_STREAM_KEEPALIVE_MAX_COUNT = 6

## Change 3: Session cleanup after response.failed

Two additional changes (commit 8c39cfa) prevent session contamination:

1. `_detach_http_bridge_request` now unconditionally calls `_release_websocket_response_create_gate` (moved before the `if not detached:` guard). Previously, if `_pop_terminal_websocket_request_state` had already removed the request from `pending_requests`, the gate was never released because `detached` was False.

2. `_finalize_websocket_request_state` now sets `reconnect_requested = True` and `retire_after_drain = True` for all `response.failed`, `response.incomplete`, and `error` events (not just `settlement.account_health_error`). This ensures the bridge session is retired after any terminal error, forcing the next request to create a fresh session with a new upstream WebSocket.

### Why this matters

Without these changes, an upstream that rejects a model (e.g. `invalid_request_error`) leaves the bridge session in `_http_bridge_sessions` with its WebSocket still open. The upstream silently drops subsequent requests on that connection, causing `:keepalive`. The client never receives a terminal event — the stream loops keepalives until the client's own watchdog kills it.

## Testing

- All 122 existing HTTP bridge unit tests pass
- All 6 keepalive-specific tests pass
- Ruff lint clean
- MyPy/pyright not available in the dev environment; type-check manually if needed

## Files Touched

| File | Lines | Change |
|------|-------|--------|
| `app/modules/proxy/service.py` | +32 / -5 | Added 2 constants, timeout wrapper in prewarm loop, keepalive counter + termination in bridge session events |

## Rollout Considerations

- The 60s keepalive limit is conservative. If legitimate long-running Codex requests regularly take >60s to produce the first token, bump `_STREAM_KEEPALIVE_MAX_COUNT` via settings or increase the constant.
- The prewarm timeout (2s) is deliberately short. The prewarm is only an optimization; a legitimate upstream that takes >2s to respond to a warmup will simply be skipped for that turn, adding minimal latency to the actual request (the upstream needs to process the real request anyway).
- Monitor the `HTTP bridge prewarm timed out` log line. If it fires frequently in production, investigate upstream health rather than adjusting the timeout.
