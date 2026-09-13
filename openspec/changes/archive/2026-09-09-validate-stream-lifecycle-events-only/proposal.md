# Validate Stream Lifecycle Events Only

## Why

Every streamed SSE/websocket event still pays a full pydantic `OpenAIEvent`
validation per layer, even though every consumer of the validated model reads
it only on stream lifecycle frames: usage/token settlement
(`response.completed`/`response.incomplete`), error normalization and
retry/health classification (`response.failed`/`error`), and websocket
response-id assignment (`response.created`). Delta frames — the dominant
traffic by two to three orders of magnitude — only ever need their `type`
string, which the already-parsed payload dict provides. On top of that, the
core client validated each chunk a second time solely to duplicate a terminal
check it had already computed from the dict, the websocket relay extracted and
re-parsed each frame's JSON twice, built a canonical SSE re-encode per frame
just to populate a discarded argument, re-validated each frame inside the
parallel-tool-call rewrite, and re-encoded `json.dumps` on every matched frame
even when the response-id rewrite changed nothing. This is the follow-up the
`2026-07-13-optimize-sse-single-parse` design doc deferred ("threading a
parsed-event struct across layer boundaries").

## What Changes

- `app/core/openai/parsing.py` gains the shared `_LIFECYCLE_EVENT_TYPES`
  frozenset (`response.created`, `response.completed`, `response.incomplete`,
  `response.failed`, `error`) and `classify_event_type(payload)`, the dict-only
  classifier that `_event_type_from_payload` and
  `tool_call_dedupe.event_type_from_payload` now delegate to.
- Streaming mixin, websocket relay, and HTTP-bridge upstream reader classify
  each frame from the parsed dict first and run `parse_sse_event_payload`
  only for lifecycle frames; all other frames flow with `event=None`, which
  every downstream branch already guards for.
- Core client: the redundant per-chunk `parse_sse_event` terminal checks are
  deleted (the `normalized_event_type` dict branch is the same check from the
  same payload); the websocket receive loops detect terminal frames from
  `parse_sse_data_json` + the payload `type` string.
- Websocket relay: single `json.loads` per frame (no synthetic-block re-parse),
  the caller's `event` is passed into `rewrite_parallel_tool_call_text` and the
  rewrite helpers no longer re-validate on the unchanged path, the discarded
  `format_sse_event` argument is no longer built, and the downstream
  response-id re-encode is skipped when the rewrite returned the payload
  unchanged (identity), relaying the upstream frame bytes as-is.
- No output-byte change on the SSE paths: canonical `format_sse_event`
  serialization, usage settlement, error rewriting, and terminal detection are
  unchanged. Existing streaming/websocket suites pass unmodified.

## Capabilities

### New Capabilities

(none)

### Modified Capabilities

- `responses-api-compat`: the single-parse streaming requirement is extended —
  schema validation of parsed stream payloads MUST run only for lifecycle
  frames, with all other frames classified from the parsed payload dict and
  all downstream semantics (framing, settlement, error normalization)
  unchanged; identity websocket relay frames are forwarded without a canonical
  re-encode.

## Impact

- **Code**: `app/core/openai/parsing.py`, `app/core/clients/proxy.py`,
  `app/modules/proxy/tool_call_dedupe.py`,
  `app/modules/proxy/_service/support.py`,
  `app/modules/proxy/_service/streaming/mixin.py`,
  `app/modules/proxy/_service/websocket/mixin.py`,
  `app/modules/proxy/_service/http_bridge/upstream_events.py`.
- **Behavior**: none on SSE surfaces. Websocket relay frames whose matched
  response-id rewrite is an identity are now forwarded with the upstream JSON
  text instead of a canonical `json.dumps` re-encode (JSON-equivalent; frames
  without a matched request state were already forwarded verbatim).
- **Performance**: removes 1–2 pydantic validations and 1–2 redundant JSON
  parses per delta frame per layer, plus one wasted `format_sse_event` and one
  wasted `json.dumps` per websocket frame.
