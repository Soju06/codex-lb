# Relay Unmodified SSE Frames Verbatim

## Why

Even after lifecycle-only validation (`validate-stream-lifecycle-events-only`),
every streamed SSE frame still pays one `json.loads` per owning layer plus an
unconditional `format_sse_event` re-encode in the streaming mixin, and the
core client parses every frame's payload just to read its `type` for terminal
detection. The dominant traffic — text/reasoning/tool-argument delta frames
after the first visible token — has no per-event consumer at all: tool-call
rewrite and duplicate suppression act only on `response.output_item.*`,
text-done suppression only on `response.output_text.done` /
`response.content_part.done`, service-tier attribution only on response
snapshots carrying `"service_tier"`, TTFT only while the first-token window is
open, and settlement/error handling only on lifecycle frames. This is the
remaining half of the follow-up the `2026-07-13-optimize-sse-single-parse`
design doc deferred, and py-spy attributes the `format_sse_event` `json.dumps`
leaf plus 2–3 redundant `json.loads` per chunk to it.

## What Changes

- `app/core/utils/sse.py` gains `sse_event_type_from_block`: cheap event-type
  extraction that matches only the exact canonical block shape
  `format_sse_event` emits (leading `event: <type>` line, single JSON-object
  `data:` line, LF framing). Data-only blocks, multi-line data, CR/CRLF
  framing, and `event:` fields appearing after `data:` (legal SSE, but not
  canonical here) return `None` so callers fall back to a full parse.
- Streaming mixin hot loop: compute the cheap type first; run the full
  parse only when the type is unavailable, is in the must-parse set
  (lifecycle/terminal frames, `response.output_item.added`/`done`,
  `response.output_text.done`, `response.content_part.done`), the TTFT
  first-token window is open (including a pending reasoning-delta window), or
  the raw line carries the `"service_tier"` marker. Otherwise the upstream
  block is yielded verbatim — raw UTF-8 and upstream key order/spacing instead
  of the `ensure_ascii` canonical re-encode — with text-visibility accounting
  set from the cheap type. The first-event block stays fully parsed.
- Core client `_normalize_stream_payload_for_http_block` becomes lazy: a
  canonical non-error block without an `"error"` substring returns its cheap
  type with no JSON parse; error frames, error-envelope payloads, alias types,
  and non-canonical framing keep the full parse + rewrite path.
- Core client `_normalize_sse_event_block` narrows its gate from `'"type":'`
  (matches every event, gates nothing) to the three legacy alias substrings,
  and — in the same change, because verbatim relay would otherwise expose the
  latent bug — rewrites the stale `event:` framing line alongside the `data:`
  payload when an alias fires.
- The chat/completions bridge and the `/v1` public normalizer are untouched;
  the `/v1` identity pass-through gate (parsed-payload object identity +
  canonical framing prefix) accepts verbatim upstream blocks unchanged.

## Capabilities

### New Capabilities

(none)

### Modified Capabilities

- `responses-api-compat`: the single-parse streaming requirement gains the
  verbatim-relay condition — a canonically framed frame that no per-event
  consumer needs parsed MAY be relayed with upstream bytes verbatim
  (JSON-equivalent, SSE-valid); all other frames keep the parse +
  canonical-re-serialization path, and legacy alias rewrites MUST cover both
  the `data:` payload and the `event:` framing line.

## Impact

- **Code**: `app/core/utils/sse.py`, `app/core/clients/proxy.py`,
  `app/modules/proxy/_service/streaming/mixin.py`.
- **Behavior**: byte-visible but JSON-equivalent — unmodified delta frames now
  carry upstream bytes (raw UTF-8, upstream key order/spacing) instead of the
  `ensure_ascii` canonical re-encode. Framing stays SSE-valid and named-event
  clients keep seeing `event:` lines (non-canonical blocks still get
  re-framed). Legacy `response.text.delta`-style upstreams now get a correct
  `event:` line after alias rewrite (previously stale, masked by the mixin
  re-encode).
- **Performance**: removes the per-delta `json.loads` in the core client and
  the mixin plus the per-delta `format_sse_event` re-encode for the dominant
  post-first-token delta traffic.
