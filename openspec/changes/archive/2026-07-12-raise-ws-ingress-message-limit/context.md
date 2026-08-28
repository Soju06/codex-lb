# Context: raise-ws-ingress-message-limit

## Incident (2026-07-12)

A Codex Desktop remote session (`codex_chatgpt_ios_remote`, client 0.144.1) running a visual-QA workflow hit `413 Request Entity Too Large` (nginx HTML) on `POST /backend-api/codex/responses`. Reverse-proxy access logs and codex-lb container logs showed the full chain:

1. `11:45:29–48Z` — six websocket upgrades to `/backend-api/codex/responses`, each `101` then closed after 2–3 s with ~4 bytes returned. codex-lb logged only `[accepted] → connection open → connection closed`; no application log, no `request_logs` row. The client's reconnect resend of full history (inline screenshots) exceeded uvicorn's default `ws_max_size` of 16 MiB, so the protocol layer closed with `1009` before the app saw the message.
2. `11:45:48–57Z` — the client exhausted its 5-retry budget, downgraded the session to HTTP (`Falling back from WebSockets to HTTPS transport`), and re-POSTed the same body six times; nginx `client_max_body_size 20m` rejected each with `413`.
3. `11:49Z` — a fresh client session reconnected over websocket and worked (new session ⇒ websocket re-enabled ⇒ incremental sends fit again).

Same-day corroboration that real payloads cross 16 MiB: `Slimmed response.create ... original_bytes=16450756 slimmed_bytes=11484111 historical_images_slimmed=5`.

## Official client evidence (openai/codex, codex-rs @ main 2026-07-11)

- One `serde_json` text message per request; no chunking, no outgoing size cap, no pre-send size check (`codex-api/src/endpoint/responses_websocket.rs`). Image mitigation happens only at capture time (downscaling in `core/src/image_preparation.rs`); history keeps full base64 data URLs.
- The client always offers `permessage-deflate` and compresses outbound frames when the server accepts (`WebSocketConfig` + `DeflateConfig::default()` in the pinned tungstenite fork). Client receive limits: 64 MiB/message, 16 MiB/frame.
- Close code `1009` is discarded (`Message::Close(_)` → generic `ApiError::Stream`); any pre-`response.completed` close is retryable. Retries reconnect and resend the **full** payload (reconnect clears incremental-send state), so an oversized request deterministically burns all 5 retries. After exhaustion, `force_http_fallback` sets `disable_websockets = true` for the rest of the session; nothing re-enables it (`core/src/client.rs`, `core/src/responses_retry.rs`).
- Wrapped websocket error events `{"type":"error","status":<n>,...}` map to HTTP-status errors; `status` is required — events without it are silently ignored until the 300 s idle timeout. Status `400` → `CodexErr::InvalidRequest`: non-retryable, surfaced immediately, session stays on websocket. Status `413` → retryable → 5 resends + sticky HTTP downgrade. No 413/payload-size error triggers auto-compaction (compaction is token-count-driven; the only error-driven compaction arm is `context_length_exceeded` inside `response.failed`).

## Why 400 supersedes the archived 413 choice

The archived change `2026-04-14-guard-oversized-response-create` picked `413` for the local rejection, before the client's status-dependent retry behavior was mapped; that delta also never got synced into the main `responses-api-compat` spec. With the mapping known, `413` is the worst available status for this failure (retry storm + transport downgrade), and `400`/`invalid_request_error` is both what the client handles best and the same shape upstream uses for invalid requests. Emitting `context_length_exceeded` to arm client auto-compaction was considered and rejected: it misrepresents the failure and can push the client into remote compaction, which resends full history and has its own upstream 413 exposure.

## Deployment notes

- Default ingress budget 128 MiB = parity with `max_decompressed_responses_body_bytes`; deployments with tight memory can lower `--ws-max-size` / `UVICORN_WS_MAX_SIZE`.
- Front proxies should raise HTTP body limits to match (nginx `client_max_body_size 128m`) because the client's HTTP fallback and remote-compaction POSTs carry full history.
- The request-decompression middleware's HTTP `413` for compressed-body overflow is intentionally unchanged: the official client only zstd-compresses request bodies against the built-in OpenAI provider, so codex-lb deployments do not exercise it; revisit if that changes.
