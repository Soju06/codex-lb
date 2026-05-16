## Why

`codex-lb`'s public `/v1/responses` endpoint is advertised as OpenAI-compatible (README: "OpenAI Python SDK", "OpenCode", "OpenClaw" all point at `/v1`). But the streaming path forwards the upstream Codex backend's SSE event stream with only per-event normalization — it never reconciles the *stream-level shape* the OpenAI SDK parser requires. An audit against the OpenAI Python SDK (`openai>=2.16`) `ResponseStreamState` parser found two stream-level contract gaps:

1. **Codex-internal events leak as the first SSE event.** The Codex backend emits a non-standard `codex.rate_limits` event, and the backend frequently (not always — it is throttled per rate-limit window) places it *before* `response.created`. `_normalize_public_stream_payload` has a catch-all `return payload, None` that passes through any event type it does not explicitly handle, so `codex.rate_limits` reaches the client verbatim. The OpenAI SDK's `_create_initial_response` raises `RuntimeError: Expected to have received 'response.created' before 'codex.rate_limits'` on the very first event — the stream never starts. Because the upstream throttles the event, the same request intermittently works or fails, which is the hardest failure mode to diagnose. Verified against `codex.nekos.me`: 4/5 streaming request shapes (plain text, tool call, structured output, error) fail; the 5th passed only because the rate-limit window had already been consumed.

2. **Streamed `response.completed` carries an empty `output` array.** The Codex backend sends `response.completed` with `output: []` and relies on the intermediate `response.output_item.done` events to carry the real items. The non-streaming path already reconstructs output via `_collect_responses_payload` / `_merge_collected_output_items`, but the streaming normalizer (`_normalize_public_responses_stream`) has no equivalent backfill. The OpenAI SDK's `get_final_response()` reads `event.response` directly from `response.completed`, so SDK consumers of the streaming endpoint get `final_response.output == []` even on a fully successful turn.

These are not Codex-client problems — the `/backend-api/codex/*` routes legitimately need `codex.rate_limits` and the Codex CLI's own stream handling. They are specifically `/v1` public-surface gaps: the `/v1` normalizer must produce a stream that conforms to the documented OpenAI Responses SSE contract.

## What Changes

- **Drop Codex-internal stream events on the `/v1` public surface.** `_normalize_public_stream_payload` MUST drop event types that are not part of the public OpenAI Responses SSE contract — specifically the `codex.`-prefixed vendor events (`codex.rate_limits`, and any future `codex.*`). The `/backend-api/codex/*` routes are unaffected and continue to forward these events.
- **Backfill streamed `response.completed` / `response.incomplete` output from item events.** `_normalize_public_responses_stream` MUST track `response.output_item.done` events and, when the terminal `response.completed` / `response.incomplete` payload carries an empty or missing `output`, reconstruct `output` from the collected item events — mirroring the existing non-streaming `_collect_responses_payload` behavior.
- **Synthesize `response.created` when the upstream stream starts with a non-created event.** When the first standard event the public stream would emit is not `response.created` (e.g. the Codex backend jumps directly to `response.failed` on upstream rejection mid-stream), `_normalize_public_responses_stream` MUST synthesize a `response.created` envelope from whatever `response` payload is available so the OpenAI SDK parser can begin processing the stream. The OpenAI Python SDK's `_create_initial_response` raises `RuntimeError` when the first event is anything other than `response.created`, breaking even legitimate error-stream consumers.
- **Add regression coverage** in `tests/unit/test_proxy_api_responses_contract.py` for all three gaps, plus an end-to-end test in `tests/e2e/` that drives the real `openai` Python SDK (`client.responses.stream(...)` and `client.responses.create(...)`) through the in-process ASGI app across plain-text, tool-call, structured-output, and error-stream shapes, asserting the SDK parser survives and `get_final_response().output` is populated.

## Capabilities

### Modified Capabilities

- `responses-api-compat`: the public `/v1/responses` streaming surface MUST emit only OpenAI Responses SSE contract events (no Codex-internal events) and MUST backfill terminal `output` from streamed item events.

## Impact

- **Code**: `app/modules/proxy/api.py` (`_normalize_public_stream_payload`, `_normalize_public_responses_stream`)
- **Tests**: `tests/unit/test_proxy_api_responses_contract.py`, `tests/e2e/test_v1_responses_openai_sdk.py` (new)
- **Behavior**: `/v1/responses` streaming responses become parseable by the stock OpenAI Python SDK in all request shapes; `/backend-api/codex/*` is unchanged.
- **Non-goals**: No change to upstream transport selection (WebSocket / HTTP bridge), no change to `/backend-api/codex/*` event forwarding, no change to non-streaming `/v1/responses` (already correct per audit).
