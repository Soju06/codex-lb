## Why

Each streamed SSE event was JSON-parsed multiple times and re-serialized even when unmodified, all on the shared event loop: the streaming mixin parsed every event twice (`parse_sse_data_json` + `parse_sse_event`, which re-parses internally), the parallel-tool-call rewrite parsed it a third time even when it changed nothing, the bridge upstream reader repeated the same pattern, and the /v1 response normalizers parsed and re-serialized every event again. At a few hundred events/second across concurrent streams this redundant CPU inflates inter-token latency.

## What Changes

- New `parse_sse_event_payload(payload)` validates an already-parsed payload; the streaming mixin and bridge upstream reader now JSON-parse each event exactly once and validate from that payload.
- `rewrite_parallel_tool_call_sse_line` / `rewrite_parallel_tool_call_text` accept the caller's parsed event, return it untouched on the no-change path, and validate from the rewritten payload (not a re-parsed string) on the change path.
- `/v1` public normalizer passes the original block through when the payload is provably unmutated (identity check — every mutating branch copies the dict) and no synthetic deltas precede it; the reasoning-summary normalizer buffers raw blocks alongside payloads and replays them verbatim on clean flushes.
- The mixin's final canonical `format_sse_event` is intentionally KEPT: it is the single serialization that guarantees stable downstream framing, and chained normalizers rely on it for byte-identical pass-through.
- Measured at the mixin layer: 21.4 → 12.1 µs per 400-byte delta event (and the old rewrite path re-parsed strings on top of that).

## Capabilities

### New Capabilities

(none)

### Modified Capabilities

- `responses-api-compat`: streaming hot paths MUST parse each SSE event's JSON at most once per process layer and MUST NOT re-serialize events that no layer modified, with framing and payload semantics unchanged.

## Impact

- **Code**: `app/core/openai/parsing.py`, `app/modules/proxy/tool_call_dedupe.py`, `app/modules/proxy/_service/streaming/mixin.py`, `app/modules/proxy/_service/http_bridge/upstream_events.py`, `app/modules/proxy/api.py`.
- **Behavior**: none — canonical mixin output is unchanged; /v1 pass-through replays the mixin's own canonical bytes.
