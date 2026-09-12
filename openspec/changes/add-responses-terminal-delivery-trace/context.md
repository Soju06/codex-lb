## Operating the trace

**Question the trace answers:** of the Responses SSE streams the proxy settled, how many had their terminal frame actually handed to the HTTP server's writer on a live connection?

```
sum by (outcome) (rate(codex_lb_stream_terminal_delivery_total{surface="responses"}[15m]))
```

`terminal_written` means "handed to uvicorn's transport buffer", not "received by the client": the socket buffer and any reverse proxy in front (HAProxy) are not observed. End-to-end confirmation still needs HAProxy termination-state logs or client telemetry.

**Joining a WARNING line to its request-log row.** The log line prints the ingress request id:

```
responses_stream_terminal_delivery request_id=<ingress id> surface=responses outcome=terminal_after_disconnect terminal=response.completed chunks=41 bytes=18211 exc=None
```

The streaming service writes that value to `request_logs.archive_request_id`; `request_logs.request_id` holds the upstream response id once `response.created` was seen, so join on `archive_request_id` (fall back to `request_id` only for rows that never saw `response.created`).

```sql
SELECT status, error_code, latency_ms, output_tokens
FROM request_logs
WHERE archive_request_id = '<ingress id>';
```

**Reading the outcomes against `request_logs.status`:**

| outcome | expected row status | meaning |
|---|---|---|
| `terminal_written` | success / error | terminal frame handed to the writer on a live connection |
| `terminal_after_disconnect` | success / error | the proxy parsed the terminal, settled, then the write was dropped by uvicorn because the peer had already gone — the production "full body, no `response.completed`, status success" shape |
| `exception_before_terminal` | success / error / cancelled | a stream wrapper raised before any terminal frame; uvicorn closes the connection without it |
| `cancelled_before_terminal` | cancelled | client left mid-stream (Starlette cancelled the body iteration; `exc=None`) or the server task itself was cancelled (`exc=CancelledError`, e.g. shutdown drain) — exclude the latter when computing a truncation rate |
| `ended_without_terminal` | any | body exhausted and `more_body=False` sent without a recognised terminal frame; a systematic count here points at framing the detector does not recognise — the known gap is a `data:`-only `error` frame relayed verbatim by the native codex passthrough (`preserve_raw_sse_line`), which settlement classifies from the JSON `type` while the detector only reads `event:` lines |

## Why the stamp lives in the protocol subclasses

The only deterministic discriminator between "the terminal was written" and "uvicorn dropped it" is `RequestResponseCycle.disconnected`, which `connection_lost` sets synchronously before any task resumes. The ASGI `receive()` channel returns `http.disconnect` after every normal completion as well, so it cannot be used. The repository already subclasses both uvicorn protocols (`app/core/http_protocol*.py`), so the stamp is a small addition to each `connection_lost`.

**Pipelining.** Stock `connection_lost` only marks `self.cycle`. In the httptools implementation that is the *newest parsed* request: a pipelined follow-up is parsed as soon as its bytes arrive, replaces `self.cycle` and is queued in `self.pipeline` while the earlier response is still streaming. The active response's Starlette disconnect listener has resumed reading (`receive()` calls `flow.resume_reading()`), so the peer's FIN/RST *is* observed — but stock uvicorn then marks only the queued request disconnected and never signals the active one, whose late terminal write is dropped by the closed transport. The httptools subclass remembers the cycle whose ASGI task is running (`_start_asgi_task` is the single hook every cycle start goes through, immediate or dequeued) and stamps the active, newest and queued cycles; `stamp_disconnect_into_scope` skips completed cycles, so duplicates are harmless. h11 needs no bookkeeping: a pipelined follow-up stays unparsed inside the h11 connection (`PAUSED`) until `start_next_cycle`, so its `self.cycle` is the active one. `test_live_server_pipelined_loss_classifies_active_terminal_dropped` reproduces the shape over real sockets (two requests in one segment, peer RST/FIN after the first chunk); before the fix it reported `terminal_written`.

`scope["state"]` identity: uvicorn creates the dict per request; `RequestIdMiddleware`, `PathRewriteMiddleware` and the dashboard auth proxy either pass the scope through or shallow-copy it while keeping the same `state` object. `MultipartContentEncodingMiddleware._without_content_encoding` is the only middleware that replaces `state`, and it is gated to route-owned multipart operations, which never reach `/responses`. A future middleware that copies `state` would silently turn `terminal_after_disconnect` into `terminal_written`; the fake-transport integration test asserts the identity through the stock uvicorn cycle, and `test_v1_responses_disconnect_stamp_reaches_traced_response_through_real_middleware_stack` asserts it through the full `app.main` middleware stack.

## Detector cost

The detector never runs a regex over a body. A canonical block starts with its `event:` line, so one anchored `match` at offset 0 decides the common case in well under a microsecond; a `bytes.find(b"\nevent: ")` (memmem) then covers coalesced or boundary-split frames — about 0.2 ms for a 512 KiB `response.output_item.done` frame versus ~17 ms for a regex `search` over the same bytes. The terminal is committed only after the server's `send` returned: uvicorn awaits its write-backpressure drain before writing, so a server-side cancel that lands inside `send` for the terminal chunk is reported as `cancelled_before_terminal` (`exc=CancelledError`), not `terminal_written`.

**Whole block, not just the `event:` line.** Nothing pins one SSE block per ASGI chunk, and an SSE client discards an event whose blank-line terminator never arrives. The detector therefore holds the terminal as *pending* once its `event:` line is seen and commits it only when the block's terminator (`\n\n`, `\r\n\r\n`, or either mixed — found with `bytes.find` on `\n`, two or three iterations for a `response.completed` block whose `data:` line is one long JSON document) has been handed over; the disconnect stamp is read after the chunk that carried the terminator. A disconnect, cancellation, or end of body between the `event:` line and the terminator is reported as `terminal_after_disconnect`, `cancelled_before_terminal`, or `ended_without_terminal` respectively. Only bytes *after* the `event:` line are searched for the terminator, so the previous frame's blank line sitting in the rolling tail cannot close the terminal block. Bare-CR line framing is neither emitted nor relayed by the proxy and is not recognised.

## Why no settlement change

Setting `terminal_event_seen` after the terminal `yield` would flip the affected rows to `cancelled` / `client_disconnected`, make the retry layer skip terminal settlement (it gates on `status in {success, error}`) and under-count consumed usage. Settlement stays as specified; this change only measures.
