# Proposal: raise-ws-ingress-message-limit

## Why

Codex clients send each Responses turn as a single websocket text message containing the full `response.create` payload. On reconnect (connection loss, the upstream 60-minute connection limit, or a new process) the official client resends the entire conversation history — including inline base64 screenshots — in that one message, with no client-side size guard, chunking, or pre-check. codex-lb currently runs uvicorn with the default `ws_max_size` of 16 MiB, so any larger inbound message is killed at the protocol layer with close code `1009` before the application ever sees it. The official client treats that close as a generic retryable stream error: it burns 5 full-payload retries and then permanently downgrades the session to HTTP transport, where the same oversized body then hits reverse-proxy body-size limits (observed in production as nginx `413` on 2026-07-12). The application-level slimming guard that exists precisely for oversized `response.create` payloads never gets a chance to run.

## What Changes

- Make the downstream websocket ingress message limit configurable (`--ws-max-size` CLI flag / `UVICORN_WS_MAX_SIZE` env var) and raise the default to 128 MiB, matching the existing HTTP responses-path decompressed body cap (`max_decompressed_responses_body_bytes`), so oversized `response.create` messages reach the application-level slimming guard instead of dying with a protocol-level `1009` close.
- Change the local oversized-`response.create` rejection status from `413` to `400` (envelope unchanged: `code = "payload_too_large"`, `type = "invalid_request_error"`, `param = "input"`). The official Codex client maps status `400` to a non-retryable invalid-request error surfaced to the user immediately, keeping the session on websocket transport; status `413` triggers 5 futile full-payload resends followed by a sticky session-wide websocket→HTTP downgrade.
- Document reverse-proxy sizing guidance (body-size limits must cover the HTTP fallback path; websocket upgrade headers) in the responses-api-compat ops context.

## Capabilities

### New Capabilities

(none)

### Modified Capabilities

- `responses-api-compat`: adds a requirement for the downstream websocket ingress message budget (configurable, 128 MiB default, applied to the decompressed message size with permessage-deflate still negotiated), and adds the oversized-`response.create` local rejection requirement with fail-fast `400` semantics. The `400` status supersedes the `413` chosen by the archived change `2026-04-14-guard-oversized-response-create`, whose delta was never synced into the main spec.

## Impact

- `app/cli.py`: new `--ws-max-size` argument (env `UVICORN_WS_MAX_SIZE`, default 128 MiB) passed to `uvicorn.run(ws_max_size=...)`, following the existing `--timeout-keep-alive` pattern.
- `app/modules/proxy/_service/response_create.py`: `_enforce_response_create_size_limit` raises `ProxyResponseError(400, ...)` instead of `413`.
- Tests: `tests/unit/test_cli.py` (flag/env plumbing), `tests/unit/test_proxy_utils.py`, `tests/integration/test_proxy_websocket_responses.py`, `tests/integration/test_http_responses_bridge.py` (guard status assertions move from 413 to 400).
- Docs: `openspec/specs/responses-api-compat/ops.md` reverse-proxy sizing guidance.
- Operators: deployments fronted by nginx should raise `client_max_body_size` to 128m to match; no breaking API change (only the status code of a local error envelope changes, in a direction the official client handles strictly better).
