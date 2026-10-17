## Why

Every event an HTTP-bridge session receives from the upstream Responses websocket
is one JSON document per text frame, yet the reader wraps it as `data: <text>\n\n`,
runs the full SSE line parser over the synthesized block, and then — for every
event routed to a pending request — re-serializes the parsed dict with
`json.dumps(ensure_ascii=True)` even when nothing about the payload changed. The
response-id rewrite helper already documents an identity fast path ("callers skip
re-serialization when the original payload object comes back"), but the call site
never honored it.

Downstream, the same block is then parsed three more times by three generator
stages that only share an `AsyncIterator[str]`: the bridge stream consumer, the
reasoning-summary normalizer, and the public Responses normalizer.

On the production profile (soju07, 60 s GIL sample, 1085 samples) the reader
parse+re-dump and the triple downstream parse together account for roughly
2.5-3.5% of GIL time. A 300-event micro-benchmark shows the reader path at
~9-11 us/event and the consumer chain at ~15-27 us/event.

## What Changes

- The bridge reader parses each upstream frame directly as a JSON object when the
  frame is a single-line object (the only shape upstream emits); every other
  input keeps the exact SSE `data:` field semantics, so `None` results and
  multi-line handling are unchanged.
- When the downstream response-id rewrite returns the identical payload object,
  the relayed block is framed from the upstream JSON text (or the tool-call
  rewrite's compact re-dump) instead of being re-serialized. The `event:` line is
  derived from the payload's string `type`, exactly as `format_sse_event` does;
  typeless `{"error": ...}` frames stay data-only.
- **Observable byte change**: relayed event `data:` lines are now the upstream
  UTF-8 JSON text verbatim. For ASCII-only frames this is byte-identical to the
  previous `json.dumps` output; for frames containing non-ASCII characters the
  text is no longer `\uXXXX`-escaped. The JSON value is identical in every case
  and every downstream consumer re-parses JSON. Rewritten payloads (response-id
  alignment, tool-call dedupe, error masking) continue to be re-serialized.
- The bridge stream consumer attaches the payload it already parsed to the block
  it yields (`ParsedSseBlock`, a `str` subclass); `parse_sse_data_json` returns
  that payload directly, so the two API-layer normalizers stop re-parsing
  pass-through blocks. The shared payload is read-only by contract; all existing
  consumers copy before mutating, and a test pins that with frozen payloads.

## Impact

- Affected specs: `responses-api-compat` (downstream SSE framing of relayed
  bridge events).
- Affected code: `app/core/utils/sse.py`,
  `app/modules/proxy/_service/http_bridge/upstream_events.py`,
  `app/modules/proxy/_service/http_bridge/streaming.py`,
  `app/modules/proxy/api.py` (comment-block fast path only).
- No new settings, no migration, no dashboard surface.
- Durable bridge spools store the relayed block text, so transcripts written
  after this change carry UTF-8 rather than escaped text for non-ASCII frames;
  replay re-parses JSON and the request fingerprint covers request text only, so
  no cross-version comparison is affected.
