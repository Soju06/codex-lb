# Tasks — relay-unmodified-sse-frames-verbatim

## 1. Implementation

- [x] 1.1 `sse_event_type_from_block` in `app/core/utils/sse.py`: strict
      canonical-shape matcher (leading `event:` line, single JSON-object
      `data:` line, LF framing); `None` otherwise
- [x] 1.2 Streaming mixin hot loop: verbatim relay branch gated on cheap type
      ∉ must-parse set, TTFT window settled (`latency_first_token_ms` set and
      no pending reasoning deltas), and no `"service_tier"` marker; keeps
      reservation touch + text-visibility accounting; first-event block stays
      fully parsed
- [x] 1.3 `_normalize_stream_payload_for_http_block`: lazy cheap-type return
      for canonical non-error, non-alias blocks without an `"error"`
      substring
- [x] 1.4 `_normalize_sse_event_block`: gate narrowed from `'"type":'` to the
      three alias substrings; alias rewrite covers the `event:` framing line
      in addition to the `data:` payload

## 2. Validation

- [x] 2.1 Unit coverage for `sse_event_type_from_block` (canonical, raw
      UTF-8, data-only, trailing `event:` ordering, CRLF/multi-line,
      non-object data)
- [x] 2.2 Mixin regressions: raw-UTF-8 delta relayed byte-identically with no
      JSON parse after TTFT settles; data-only delta re-framed with
      `event: <type>` (5ee532cb regression class); usage settlement unchanged
- [x] 2.3 Client normalizer regressions: canonical frames skip `json.loads`;
      `error` frames and top-level error envelopes still rewritten; alias
      rewrite covers both lines; non-alias blocks skip the alias parse
- [x] 2.4 `/v1` identity pass-through accepts verbatim raw-UTF-8 blocks
      byte-identically
- [x] 2.5 Existing streaming/contract/dedupe/TTFT suites pass; `uvx ruff
      format --check`, `uv run ruff check` on changed files
