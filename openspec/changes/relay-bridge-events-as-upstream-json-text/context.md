# Context

## Where the cost was

Production GIL profile (soju07, 2x Neoverse-N1, `workers=1`, 60 s, 1085 samples):

- `_process_http_bridge_upstream_text -> parse_sse_data_json` 13 samples and the
  unconditional `format_sse_event` at the matched-request site 11 samples (2.2%).
- `parse_sse_data_json` under `_stream_http_bridge_session_events`,
  `_normalize_reasoning_summary_stream`, and `_normalize_public_responses_stream`
  another ~28 samples (2.6%): three full parses of a block whose payload was
  already known.

## Why the fast path is safe

- Upstream websocket frames are one JSON document each. `parse_sse_data_json_text`
  only takes the direct path when the text starts with `{` and contains no CR/LF,
  which is exactly the case where the SSE field parser would have produced the
  same string back; everything else (blank, `[DONE]`, pretty-printed, leading
  whitespace) still goes through the field parser, so `None` semantics and the
  multi-line join are unchanged. A Hypothesis test pins the equivalence.
- `format_sse_event_from_text` requires that `text` is a serialization of
  `payload`. At the call site this holds because the payload was parsed from that
  text, or the tool-call rewrite replaced both together with a compact re-dump.
  The `event:` line uses the same `isinstance(type, str) and type` rule as
  `format_sse_event`, not `classify_event_type`, so typeless error frames remain
  data-only exactly as before.
- Bytes differ from the previous output only for non-ASCII frames (UTF-8 instead
  of `\uXXXX`). Nothing hashes relayed event text; the durable fingerprint covers
  request text only. `sse_event_type_from_block` and
  `_has_canonical_event_framing` match on the `event:` line and accept raw UTF-8.

## The carrier

`ParsedSseBlock` is a plain `str` subclass with a `payload` attribute. It behaves
like `str` for framing checks, `encode`, equality, and the ASGI body writer.
Derived strings (`strip()`, concatenation) are plain `str`, so a stage that
rewrites a block cannot accidentally forward a stale payload. Consumers must
treat the payload as read-only; the API-layer normalizers already copy before
mutating (`dict(payload)`), and
`test_normalize_public_responses_stream_reuses_carried_payload_without_mutating_it`
runs both normalizers over deep-frozen payloads to keep that true.

## Measured (300-event stream, this venv, CPython 3.14)

| path | before | after |
|---|---|---|
| reader parse + frame, ASCII | ~2.8-3.1 ms | ~1.1 ms |
| reader parse + frame, Korean | ~3.3-3.5 ms | ~1.75 ms |
| 3-stage consumer parse, ASCII | ~4.7-7.9 ms | ~2.2 ms |
| 3-stage consumer parse, Korean | ~5.9-8.3 ms | ~2.1 ms |
