## 1. Protocol disconnect stamp

- [x] 1.1 Add `HTTP_DISCONNECTED_STATE` and `stamp_disconnect_into_scope(cycle, exc)` to `app/core/http_protocol.py` (no-op when the cycle is absent or the response is complete; writes only into the existing per-request `scope["state"]`).
- [x] 1.2 Call the stamp after `super().connection_lost(exc)` in `UpgradeTolerantH11Protocol` and `UpgradeTolerantHttpToolsProtocol`; extend both module docstrings with the third reason the subclasses exist.

## 2. Traced streaming response

- [x] 2.1 Create `app/modules/proxy/downstream_delivery.py` with `DeliveryTracedStreamingResponse`: wrapped `send`, chunk/byte counters (non-empty chunks whose `send` returned), rolling-tail terminal-event detection on `event:` lines (anchored match at offset 0 + `bytes.find` prefilter, no regex walk over large frames), terminal committed only after `send` returned, `more_body=False` tracking, closed five-outcome classification, one log line + one counter increment in a synchronous `finally` (no clock reads, no awaits).
- [x] 2.2 Register `stream_terminal_delivery_total` (`codex_lb_stream_terminal_delivery_total{surface,outcome}`) in `app/core/metrics/prometheus.py` with the `None` fallback and `__all__` entry.
- [x] 2.3 Return `DeliveryTracedStreamingResponse(stream, surface="responses", ...)` from `_stream_responses` in `app/modules/proxy/api.py`.

## 3. Tests

- [x] 3.1 `tests/unit/test_downstream_delivery.py`: one test per outcome, every terminal event type (including CRLF framing), a frame split across chunks, look-alikes inside a `data:` payload (single chunk and split at the boundary), a 512 KiB non-terminal frame costing one anchored match (no `search`), disconnect stamp before vs. after the terminal write, exception re-raised without a synthetic `more_body=False`, exception after the terminal, server cancel while `send` waits on the terminal chunk, cancel after the terminal, client `http.disconnect` on the ASGI 2.3 path, missing `scope["state"]`, no-`prometheus_client` no-op, real `Counter` registration when the `metrics` extra is installed, closed label set.
- [x] 3.2 `tests/integration/test_http_disconnect_stamp.py`: fake-transport tests over both protocol subclasses (FIN -> `eof`, RST -> `ConnectionResetError`, no stamp after a completed response, stock teardown unchanged, non-streaming cycle stamped) and a live-socket test with the production protocol wiring where the client half-closes after the first SSE chunk (`terminal_after_disconnect`, no terminal or `0\r\n\r\n` received) plus a control run (`terminal_written`).
- [x] 3.3 `tests/integration/test_proxy_responses.py`: `/v1/responses` through the real app returns a `DeliveryTracedStreamingResponse` and logs `outcome=terminal_written`; a hand-built ASGI scope whose `state` is stamped after the first chunk yields `terminal_after_disconnect` through the full middleware stack.
- [x] 3.4 Keep `tests/integration/test_http_keepalive_timer.py`, `tests/integration/test_http_upgrade_tolerance.py`, `tests/unit/test_cli.py` and the Responses contract suite green.

## 4. Validation

- [x] 4.1 `uv run ruff check .`, `uv run ruff format --check .`, `uv run ty check`.
- [x] 4.2 `scripts/check_proxy_architecture.py`, `scripts/check_cancellation_safety.py`, `scripts/check_proxy_timing_seams.py`, `scripts/check_settings_tiers.py` unchanged and green (new module has zero timing seams; no ceiling-guarded file grew).
- [x] 4.3 `openspec validate add-responses-terminal-delivery-trace --strict` and `openspec validate --specs`.
