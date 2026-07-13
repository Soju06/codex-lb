# Design: raise-ws-ingress-message-limit

## Context

Production incident (2026-07-12): a Codex Desktop remote session reconnected its Responses websocket and resent the full conversation history (visual-QA screenshots inline) as one websocket text message larger than 16 MiB. uvicorn's default `ws_max_size=16 MiB` closed each connection at the protocol layer (`[accepted] → connection open → connection closed` with no application log), the client burned 5 full-payload retries, downgraded the session to HTTP, and the fallback POST then hit the reverse proxy's `client_max_body_size` as a `413` HTML error.

Official client behavior (verified against `openai/codex` codex-rs @ main 2026-07-11 and its pinned tungstenite fork):

- One `serde_json` text message per request; no chunking, no outgoing size limit, no pre-send size check (`codex-api/src/endpoint/responses_websocket.rs`). The proxy is the only place a size guard can live.
- The client always offers `permessage-deflate` and compresses outbound frames when the server accepts it (`WebSocketConfig` + `DeflateConfig::default()`); its own *receive* limits are 64 MiB/message, 16 MiB/frame.
- Close code `1009` is discarded — any close before `response.completed` is a generic retryable stream error: up to 5 retries (each reconnecting and resending the full payload, since reconnect clears the incremental-send state), then `force_http_fallback` flips `disable_websockets=true` for the rest of the session (`core/src/responses_retry.rs`, `core/src/client.rs`).
- Error-envelope handling over the websocket (`parse_wrapped_websocket_error_event`): an event `{"type":"error","status":<n>,...}` maps to an HTTP-status transport error. Status `400` → `CodexErr::InvalidRequest`, non-retryable, surfaced immediately, session stays on websocket. Status `413` → retryable, 5 full-payload resends, then sticky HTTP downgrade. Events without a `status` field are silently ignored (stream idles out). HTTP responses behave the same way per status.

## Goals / Non-Goals

**Goals**

- Let oversized `response.create` messages reach the existing application-level slimming guard instead of dying at the protocol layer.
- When slimming cannot fit the payload, fail in the way the official client handles best: immediately visible, no retry storm, no transport downgrade.

**Non-Goals**

- No change to the upstream (proxy→chatgpt.com) websocket budget (16 MiB event/frame budget, 15 MiB `response.create` cap) — that reflects upstream's observed ceiling and stays as-is.
- No change to the request-decompression middleware's HTTP `413` for compressed-body overflow (transport-level concern; the official client only zstd-compresses against the built-in OpenAI provider, so this path is not exercised by codex-lb clients).
- No image re-compression, externalization, or upload-reference mechanism.

## Decisions

### D1: Raise uvicorn `ws_max_size` to 128 MiB, configurable via CLI/env

`app/cli.py` gains `--ws-max-size` (default from `UVICORN_WS_MAX_SIZE`, else `134217728`) passed to `uvicorn.run(ws_max_size=...)`, mirroring the existing `--timeout-keep-alive`/`UVICORN_TIMEOUT_KEEP_ALIVE` pattern (cli.py cannot import app settings before uvicorn loads the app). 128 MiB gives parity with `max_decompressed_responses_body_bytes` — the HTTP responses path already accepts this much, so the websocket path introduces no new memory-exposure class. uvicorn's `websockets` implementation enforces `ws_max_size` against the decompressed size with `permessage-deflate` (default-on, kept on) — matching how the client compresses outbound frames.

**Alternative considered**: an ASGI-level guard reading the message in chunks — rejected; uvicorn buffers whole websocket messages before handing them to the app, so there is no streaming point to intercept, and `ws_max_size` is the supported knob.

### D2: Local oversized rejection uses status 400, keeping the existing envelope

`_enforce_response_create_size_limit` raises `ProxyResponseError(400, ...)` instead of `413`. Envelope fields (`payload_too_large` / `invalid_request_error` / `param="input"`, message advising to reduce screenshots or compact) are unchanged; `type: "error"` events already carry `"status"` via `_wrapped_websocket_error_event`, which is the exact shape the client parses. Rationale: fail-fast semantics per the client evidence above. `413` was chosen by the archived change `2026-04-14-guard-oversized-response-create` before the client's status-dependent retry behavior was known; that delta was never synced into the main spec, so this change ADDs the requirement with the corrected status rather than modifying a synced one.

**Alternative considered**: emitting `response.failed` with `error.code = "context_length_exceeded"` to arm the client's auto-compaction — rejected as dishonest signaling; it can also route the client into remote compaction, which resends full history and has its own upstream 413 exposure.

### D3: Reverse-proxy guidance in ops.md

Even with websocket transport healthy, the client's sticky HTTP fallback and remote-compaction POSTs mean large HTTP bodies still occur. Ops guidance: front proxies should allow request bodies up to codex-lb's own cap (`client_max_body_size 128m` for nginx) and pass websocket upgrades (`proxy_http_version 1.1`, `Upgrade`/`Connection` headers).

## Risks / Trade-offs

- **Memory**: a single websocket message can now buffer up to 128 MiB decompressed in-process. Bounded and equal to the existing HTTP-path exposure; deployments with tight `mem_limit` can lower `--ws-max-size`.
- **Client version drift**: the 400-vs-413 behavior is pinned to current codex-rs error mapping. A future client could change retry semantics; the envelope carries an explanatory message either way, and 400/invalid_request_error is the same shape upstream uses for invalid requests, so this tracks upstream convention rather than diverging from it.
- **Messages above 128 MiB** still get a protocol-layer close and the legacy retry/fallback path. Accepted: parity with the HTTP path's hard cap; genuinely pathological payloads should fail.
