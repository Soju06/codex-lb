# Design — relay-unmodified-sse-frames-verbatim

## Context

Follow-up to `validate-stream-lifecycle-events-only` (stacked on it) and the
second half of the deferral in `2026-07-13-optimize-sse-single-parse`: after
lifecycle-only validation, every frame still pays one `json.loads` per owning
layer and an unconditional `format_sse_event` re-encode in the streaming
mixin. Delta frames after the first visible token have no per-event consumer,
so their bytes can relay verbatim.

## Goals / Non-Goals

**Goals:** zero JSON parse and zero re-encode for canonically framed frames no
consumer needs parsed; preserve every consumer's trigger surface (terminal
settlement, error rewrite, tool-call rewrite/dedupe, text-done suppression,
service-tier attribution, TTFT, reservation touches); keep the
`5ee532cb` framing guarantee (data-only blocks are re-framed with
`event: <type>` for EventSource clients); fix the stale-`event:`-line alias
bug that verbatim relay would otherwise expose.

**Non-Goals:** the chat/completions bridge (cross-dialect translation keeps
full parsing); the `/v1` public normalizer (independent per-chunk consumer —
verbatim blocks satisfy its identity gate unchanged, see below); the websocket
relay and bridge upstream reader (every ws frame is parsed for response-id
multiplexing; covered by the stacked lifecycle change); the first-event block
in the streaming mixin (once per stream; retry classification lives there).

## Decisions

- **Cheap type = strict canonical shape.** `sse_event_type_from_block` matches
  only `event: <type>\ndata: {…}\n\n` (single data line, LF framing,
  JSON-object data). SSE legally allows the `event:` field after `data:`,
  CR/CRLF framing, comments, and multi-line data — all of those return `None`
  and take the existing full-parse path, so only blocks byte-shaped like
  `format_sse_event` output (which is what the upstream Codex backend and our
  own re-encodes emit) are eligible for verbatim relay. This resolves the
  field-ordering checklist item.
- **Must-parse set** = lifecycle/terminal frames (`response.created`,
  `response.in_progress`, `response.completed`, `response.failed`,
  `response.incomplete`, `error`) + tool-call item frames
  (`response.output_item.added`, `response.output_item.done` — rewrite,
  duplicate suppression, and TTFT item inspection) + text-done frames
  (`response.output_text.done`, `response.content_part.done` — suppression
  reads the payload's `part`). Everything else has no payload consumer outside
  the gated windows below.
- **TTFT window trigger.** The full parse also runs while
  `latency_first_token_ms is None` **or** `ttft_reasoning_deltas` is
  non-empty. Verified by reading `support.py`: `_ttft_event_latency_ms` (and
  therefore all mutation of the pending reasoning-delta state) is invoked only
  under the `latency_first_token_ms is None` guard (mixin loop and first-event
  block), and the stream-end `_finalize_ttft_latency_ms` is gated on the same
  condition — so the first clause alone already covers the pending window; the
  explicit non-empty check is a defensive belt (pending entries can outlive
  TTFT settlement, e.g. a second reasoning summary stream, but are never read
  after it).
- **Service-tier gate** stays on the raw line (`'"service_tier"'` substring),
  not the event type, so a moved snapshot field still full-parses; false
  positives (the substring inside delta text) just take the parse path.
- **Verbatim branch bookkeeping.** The reservation touch (non-terminal frames
  keep reservations alive), `saw_text_delta`, `settlement.downstream_visible`,
  and `settlement.downstream_text_visible` are preserved; text flags derive
  from the cheap type, which for canonical frames equals the payload type.
- **Client normalizer laziness.** `_normalize_stream_payload_for_http_block`
  returns the cheap type without parsing only when the block is canonical, the
  type is not `error` and not a legacy alias, and the block has no `"error"`
  substring. The substring guard is load-bearing: `parse_error_payload`
  rewrites any payload carrying a top-level `error` envelope regardless of its
  `type` (`OpenAIErrorEnvelope.error` is optional, so only an actual `error`
  key triggers it), and response snapshots legitimately carry `"error":null` —
  both stay on the full-parse path.
- **Alias gate + stale `event:` line.** `_normalize_sse_event_block` now gates
  on the three bare alias names (matching both `"type":"<alias>"` in data
  lines and `event: <alias>` framing lines) instead of `'"type":'`, and
  rewrites the `event:` line too. Previously only the data line was rewritten
  and the mixin's unconditional re-encode masked the mismatch; under verbatim
  relay the stale line would reach clients, so the fix lands in the same
  change.
- **/v1 identity gate verified for raw UTF-8.** `api.py` pass-through compares
  parsed-payload *object identity* (`normalized_payload is parsed_payload`)
  plus `_has_canonical_event_framing`, which checks only the
  `event: <type>\n` prefix — no comparison against a re-serialization — so
  verbatim raw-UTF-8 blocks pass through byte-identically (regression test
  added).

## Accepted limitations (documented drift)

- **Byte-visible output change.** Unmodified delta frames now carry upstream
  bytes (raw UTF-8, upstream key order/spacing) instead of the `ensure_ascii`
  canonical re-encode. JSON-equivalent and SSE-valid; codified in the spec
  delta.
- **The `event:` framing line is trusted for non-parsed frames.** A
  hypothetical upstream frame whose `event:` line disagrees with its payload
  `type` (never emitted by upstream; our own re-encodes are consistent by
  construction) would be classified by the framing line: a terminal payload
  disguised under a delta `event:` line would relay verbatim and settle as
  `stream_incomplete` at EOF instead of a terminal settlement. Today's
  behavior for such frames differs only in which side wins; the `/v1`
  normalizer still parses independently and enforces its own contract.
- **Malformed-JSON canonical frames.** A canonical-looking block whose data is
  not valid JSON relays verbatim (today it is also yielded unchanged — the
  parse failure path skips the re-encode) but now sets `saw_text_delta` /
  text-visibility from the framing line, which the parse path would not.
  Only reachable from a misbehaving upstream.
- **Usage on delta frames** would relay verbatim without settlement capture,
  as under the stacked lifecycle change (upstream emits usage only on
  terminal frames) — unchanged hedge, inherited.

## Stacking note (delta-merge hazard)

This change is stacked on `validate-stream-lifecycle-events-only` and MODIFIES
the same `responses-api-compat` requirement. To avoid the concurrent-MODIFIED
last-writer-wins loss (#1772), this change's delta contains the **union** text:
the lifecycle-only validation clauses from the stacked change plus the
verbatim-relay condition, so syncing/archiving in either order preserves both.
