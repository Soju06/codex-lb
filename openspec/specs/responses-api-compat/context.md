# Responses API Compatibility Context

## Purpose and Scope

This capability implements OpenAI-compatible behavior for `POST /v1/responses`, including request validation, streaming events, non-streaming aggregation, and OpenAI-style error envelopes. The scope is limited to what the ChatGPT upstream can provide; unsupported features are explicitly rejected.

See `openspec/specs/responses-api-compat/spec.md` for normative requirements.

## Rationale and Decisions

- **Responses as canonical wire format:** Internally we treat Responses as the source of truth to avoid divergent streaming semantics.
- **Strict validation:** Required fields and mutually exclusive fields are enforced up front to match official client expectations.
- **No truncation support:** Requests that include `truncation` are rejected because upstream does not support it.
- **Compact as a separate contract:** Standalone compact is treated as a canonical opaque context-window contract, not as a variant of buffered normal `/responses`.

## Constraints

- Upstream limitations determine available modalities, tool output, and overflow handling.
- `store=true` is rejected; responses are not persisted.
- `include` values must be on the documented allowlist.
- `truncation` is rejected.
- `previous_response_id` is forwarded when `conversation` is absent, but the `conversation + previous_response_id` conflict remains rejected.
- HTTP `/v1/responses` and HTTP `/backend-api/codex/responses` now use a server-side upstream websocket session bridge by default so repeated compatible requests can keep upstream response/session continuity without forcing clients onto the public websocket route.
- Codex-affinity HTTP bridge sessions can optionally use a conservative first-request prewarm (`generate=false`), but that behavior now stays behind an explicit flag so production defaults do not pay an extra upstream request unless operators opt in.
- When operators configure a multi-instance bridge ring, deterministic owner enforcement now applies only to hard continuity keys such as `x-codex-turn-state` and explicit session headers. Prompt-cache-derived bridge keys remain stable for local reuse, but in gateway-safe mode a non-owner replica may tolerate that locality miss and create or reuse a local session instead of failing with `bridge_instance_mismatch`.
- Codex-facing websocket routes now advertise `x-codex-turn-state` during websocket accept and honor client-provided turn-state on reconnect so routing can stay sticky at turn granularity even when the public websocket reconnects.
- HTTP responses routes now also return `x-codex-turn-state` headers so clients that persist response headers can promote later HTTP requests from prompt-cache affinity to stronger Codex-session continuity.
- `/v1/responses/compact` keeps a final-JSON contract and preserves the raw upstream `/codex/responses/compact` payload shape as the canonical next context window instead of rewriting it through buffered `/codex/responses` streaming.
- Compact transport failures fail closed with respect to semantics: no surrogate `/codex/responses` fallback and no local compact-window reconstruction.
- Compact transport may use bounded same-contract retries only for safe pre-body transport failures and `401 -> refresh -> retry`.
- `/v1/responses/compact` is supported only when the upstream implements it.
- `prompt_cache_key` affinity on OpenAI-style routes is intentionally bounded by a dashboard-managed freshness window, unlike durable backend `session_id` or dashboard sticky-thread routing.

## Include Allowlist (Reference)

- `code_interpreter_call.outputs`
- `computer_call_output.output.image_url`
- `file_search_call.results`
- `message.input_image.image_url`
- `message.output_text.logprobs`
- `reasoning.encrypted_content`
- `web_search_call.action.sources`

## Failure Modes

- **Stream ends without terminal event:** Emit `response.failed` with `stream_incomplete`.
- **Upstream error / no accounts:** Non-streaming responses return an OpenAI error envelope with 5xx status.
- **Compact upstream transport/client failure:** Retry only inside `/codex/responses/compact` when the failure is safely retryable; otherwise return an explicit upstream error without surrogate fallback.
- **HTTP bridge session closes or expires:** The next compatible HTTP `/v1/responses` or `/backend-api/codex/responses` request recreates a fresh upstream websocket bridge session; continuity is guaranteed only within the lifetime of one active bridged session.
- **Multi-instance routing without bridge owner policy:** if operators do not configure a bridge ring or front-door affinity, continuity can still fragment across replicas. With a configured bridge ring, hard continuity keys still fail closed on the wrong replica, while gateway-safe prompt-cache requests may accept locality misses instead of failing.
- **Codex websocket reconnects:** Reconnect continuity now depends on the client replaying the accepted `x-codex-turn-state`; generated turn-state is emitted on accept for backend Codex routes and echoed back when the client already supplies one.
- **Websocket handshake forbidden/not-found:** Auto transport now fails loud on `403` / `404` instead of silently hiding the websocket regression behind HTTP fallback.
- **Invalid request payloads:** Return 4xx with `invalid_request_error`.

## Error Envelope Mapping (Reference)

- 401 → `invalid_api_key`
- 403 → `insufficient_permissions`
- 404 → `not_found`
- 429 → `rate_limit_exceeded`
- 5xx → `server_error`

## Examples

Non-streaming request/response:

```json
// request
{ "model": "gpt-5.1", "input": "hi" }
```

```json
// response
{ "id": "resp_123", "object": "response", "status": "completed", "output": [] }
```

## Operational Notes

- Pre-release: run unit/integration tests and optional OpenAI client compatibility tests.
- Smoke tests: stream a response, validate non-stream responses, and verify error envelopes.
- Post-deploy: monitor `no_accounts`, `upstream_unavailable`, compact retry attempts, and compact failure phases, especially on direct compact requests.
- Post-deploy: monitor HTTP bridge reuse/create/evict/reconnect counts and any `previous_response_not_found` or queue-saturation errors on `/v1/responses` and `/backend-api/codex/responses`.
- Post-deploy: monitor `capacity_exhausted_active_sessions`, Codex-session bridge reuse/evict counts, websocket handshake 403/404 rates after the narrower auto-fallback policy, and backend Codex HTTP vs websocket cache-ratio gaps.
- When tracing compact incidents, confirm that request logs and upstream logs show direct `/codex/responses/compact` usage without surrogate `/codex/responses` fallback.
- Post-deploy: monitor `no_accounts`, `stream_incomplete`, and `upstream_unavailable`.
- Websocket/Codex CLI tier verification runbook: `openspec/specs/responses-api-compat/ops.md`
