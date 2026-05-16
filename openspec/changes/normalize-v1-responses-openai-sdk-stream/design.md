## Overview

`codex-lb` exposes two routers that both proxy the upstream Codex Responses API:

- `/backend-api/codex/*` — for the Codex CLI itself; preserves the upstream event stream verbatim because the Codex CLI expects (and uses) `codex.rate_limits` and other vendor-specific events.
- `/v1/*` — advertised as OpenAI-compatible (see README: Codex CLI uses `/backend-api/codex`, but OpenCode, OpenClaw, and the OpenAI Python SDK all use `/v1`).

The streaming normalizer for the public `/v1` surface (`_normalize_public_responses_stream` + `_normalize_public_stream_payload`) currently does per-event normalization only. Two stream-level gaps were found by feeding live upstream traffic through the normalizer and into the OpenAI Python SDK's `ResponseStreamState` parser.

## Decisions

### 1. Drop Codex-internal events at the `/v1` boundary

`_normalize_public_stream_payload` ends with a catch-all `return payload, None`. Any event type not explicitly handled (`error`, `response.completed`, `response.incomplete`, `response.output_item.added`, `response.output_item.done`) is forwarded verbatim. This includes `codex.rate_limits`, which the upstream emits *before* `response.created` whenever the rate-limit telemetry window hasn't expired.

The OpenAI SDK parser (`ResponseStreamState._create_initial_response`) raises `RuntimeError` when the first received event is not `response.created`. Because the rate-limit event is throttled (one per window, not per request), the same request shape sometimes works and sometimes fails — the SDK never starts parsing.

**Decision:** in the `/v1` normalizer, drop any event whose `type` starts with `codex.`. This is the smallest predicate that matches all current and reasonably-future Codex vendor extensions while preserving every event type defined in the public OpenAI Responses SSE contract. Add the check at the top of `_normalize_public_stream_payload` (returning `None, None` to suppress) so the outer `_normalize_public_responses_stream` skips emission.

**Why not move the filter into the upstream client (`stream_responses`)?** The `/backend-api/codex/*` routes share `_stream_responses` and the same `_normalize_public_responses_stream` family, and the Codex CLI legitimately needs `codex.*` events and the upstream's native ordering. Adding a per-route `enforce_openai_sdk_contract: bool` parameter to `_stream_responses` (defaulting to `True`) and threading it into `_normalize_public_responses_stream` is the minimum surgery that keeps the two routes decoupled. The `/backend-api/codex/responses` route passes `enforce_openai_sdk_contract=False`; everything else (`/v1/responses`, `internal_bridge_responses`) keeps the default `True`.

**Why not normalize to a real OpenAI event (e.g. by translating to rate-limit headers)?** `/v1/responses` already exposes upstream rate-limit information via response headers (`include_rate_limit_headers=True` in `_stream_responses`). Re-emitting the same information as a stream event would be additional surface that the public OpenAI contract does not specify. Drop is the minimal correct action.

### 2. Backfill terminal `output` from streamed item events

The Codex backend emits `response.completed` with `output: []`, relying on intermediate `response.output_item.done` events to carry the real output items (messages, function calls, reasoning items, etc.).

The non-streaming path in `_collect_responses_payload` already handles this correctly: it collects `output_item.done` payloads into a dict keyed by `output_index`, then `_merge_collected_output_items` overlays them onto the terminal `response.output` when it is empty.

The streaming path does not. The OpenAI SDK's `accumulate_event` for `response.completed` calls `parse_response(response=event.response, ...)` with the raw `event.response`, so `get_final_response().output` reflects exactly what `response.completed` carried — `[]`.

**Decision:** introduce the same collect-and-merge behavior in `_normalize_public_responses_stream`. Track `response.output_item.done` events as the stream is iterated; when a `response.completed` or `response.incomplete` event arrives, if its `response.output` is empty/missing, rewrite the payload's `response.output` from the collected items before yielding the SSE block. Reuse the existing `_merge_collected_output_items` helper to keep the merge rule identical to the non-streaming path.

**Why not let the client (OpenAI SDK) accumulate from item events itself?** The SDK does accumulate items in its `accumulate_event` path via `response.output_item.added`. But the `final_response` returned by `get_final_response()` comes from `parse_response(response=event.response, ...)`, not from the internal snapshot — so even though the SDK *internally* sees the items during the stream, the user-facing `final_response.output` reflects the terminal event's `response.output`. Backfilling at the proxy is the only way to make `get_final_response().output` correct for the documented public contract.

### 3. Synthesize `response.created` when the upstream stream starts with a non-created event

This gap was discovered during Phase 0 audit *after* fix (1) landed: with `codex.rate_limits` dropped, the error-stream case (an upstream-rejection scenario that produces only `response.failed`) became the new first event. The OpenAI SDK parser raises the same `RuntimeError` for *any* first event that is not `response.created`.

**Decision:** in `_normalize_public_responses_stream`, track whether the public stream has emitted `response.created`. Before emitting any other standard `response.*` event for the first time, synthesize a `response.created` envelope from whatever `response` payload is on the current event (`response.failed` carries one; so do `response.completed` and `response.incomplete`). Set `status: "in_progress"` and `output: []` on the synthesized envelope so the SDK's accumulator starts cleanly. The original event is then forwarded unchanged.

**Why not promote the upstream rejection to an HTTP 4xx?** Upstream rejection mid-stream goes through `_probe_stream_startup_error` first, which already converts certain pre-stream rejections to HTTP responses. The class we're fixing here is *post-stream-startup* failures where the upstream connection succeeded and started sending SSE but the very first event is terminal. Treating those as HTTP errors would require unwinding an already-active stream and breaking the existing `_probe_stream_startup_error` contract; synthesizing `response.created` is the minimum that makes the SDK-parser path correct without touching transport.

**Why not drop the leading non-created event and emit only `response.created`?** Then consumers would see no terminal event and the stream would hang from the SDK's perspective until the keepalive shutdown.

**Why is the synthesized envelope safe?** `response.created` from real OpenAI carries `id`, `object`, `created_at`, `model`, `status="in_progress"`, `output=[]`. The Codex backend's `response.failed.response` payload carries the same envelope shape (verified in audit2: `body.status="completed"` for non-streaming, and `response.failed.response` carries the same fields). We copy the envelope and set `status="in_progress"`, `output=[]` — the only two fields whose values must reflect the synthesized event's contract.

### 4. Scope: streaming `/v1/responses` only

- `/backend-api/codex/*` — out of scope. Codex CLI uses these events. Confirmed in the existing image-adapter comment in `images_service.py` that mentions `codex.rate_limits` as a known passthrough event for the Codex surface.
- Non-streaming `/v1/responses` — out of scope. Audit confirmed `_collect_responses_payload` + `_merge_collected_output_items` already produce a SDK-parseable `Response` object (`output_len=2`, all items present).
- The upstream transport layer (WebSocket vs HTTP bridge) — out of scope. The gap is in the public-stream emission layer, not the upstream transport.

### 5. Test strategy

- **Unit tests** (`tests/unit/test_proxy_api_responses_contract.py`): direct exercise of `_normalize_public_responses_stream` with hand-crafted upstream blocks, asserting (a) `codex.rate_limits` does not appear in the normalized output, (b) `response.created` is the first emitted event, (c) when upstream sends `response.completed` with `output: []`, the normalized terminal event has `output` reconstructed from prior `output_item.done` events.
- **E2E tests** (`tests/e2e/test_v1_responses_openai_sdk.py`, new): drive the real `openai` Python SDK against the in-process ASGI app via `httpx.ASGITransport`, mocking `core_stream_responses` (the same pattern `tests/e2e/test_proxy_flow.py` already uses) to inject a leading `codex.rate_limits` block and an empty `response.completed.output`. Verify:
  - `client.responses.stream(...)` `with` block completes without `RuntimeError`
  - `stream.get_final_response().output` is non-empty across plain-text, tool-call, and structured-output shapes
  - `client.responses.create(...)` (non-streaming) returns a populated `Response` object
  - Error-stream case (upstream `response.failed` after `response.created`) surfaces as the SDK's `error` / failed-response path, not as a parser exception

## Risks

- **Future Codex vendor events.** Hardcoding the `codex.` prefix drop catches the known event and any future Codex-prefixed extensions. If OpenAI ever ships a real upstream event whose type happens to start with `codex.`, the drop will hide it; this is acceptable given OpenAI's existing event naming convention (all standard events use `response.*` or `error`).
- **Upstream contract drift.** If Codex starts emitting `output` directly in `response.completed`, the backfill becomes a no-op (it only fires when `output` is empty/missing), so there is no regression risk.

## Out of scope (deferred)

- Translating `codex.rate_limits` into response headers on the `/v1` surface. The data is already exposed via the same response headers used by the non-streaming path (`include_rate_limit_headers=True`); duplicating it in stream form is unnecessary.
- WebSocket transport (`/v1/responses/websocket`). Audit was HTTP-only because the gaps observed are in `_normalize_public_*`, which runs after the transport. The WebSocket path goes through the same normalizer family — a follow-up audit can confirm, but no separate fix is anticipated.
