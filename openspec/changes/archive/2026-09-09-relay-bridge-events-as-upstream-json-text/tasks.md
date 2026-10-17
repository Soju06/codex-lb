# Tasks

## 1. Parse once at the bridge reader

- [x] 1.1 Add `parse_sse_data_json_text` to `app/core/utils/sse.py`: direct
      `json.loads` for single-line JSON-object text, exact `data:` field fallback
      otherwise; property-test equivalence with `parse_sse_data_json(f"data: {text}\n\n")`
- [x] 1.2 Use it in `_process_http_bridge_upstream_text` instead of framing and
      re-parsing the upstream frame

## 2. Relay unchanged events without re-serializing

- [x] 2.1 Add `format_sse_event_from_text(payload, text)` producing the canonical
      block from already-serialized text, with `format_sse_event` fallback for
      non-canonical text
- [x] 2.2 Honor the identity contract of `_rewrite_websocket_downstream_response_id`
      in `_process_parsed_http_bridge_upstream_event`: re-serialize only when the
      payload object changed
- [x] 2.3 Always re-serialize `response.output_item.done` events: the parallel
      tool-use dedupe trims duplicated tool uses by mutating the payload in place,
      so the upstream text can be stale for that event type

## 3. Carry the parsed payload downstream

- [x] 3.1 Add `ParsedSseBlock` (str subclass) and `sse_block_with_payload`;
      `parse_sse_data_json` returns the carried payload
- [x] 3.2 Yield carrier blocks from `_stream_http_bridge_session_events`
- [x] 3.3 Fast-path canonical `event:`/`data:` blocks in `_looks_like_sse_comment_block`

## 4. Verification

- [x] 4.1 Reader parity tests: non-ASCII/U+2028 relay is JSON-equivalent to the
      previous re-serialized block, ASCII relay is byte-identical, rewritten
      response ids are re-serialized
- [x] 4.2 Carrier survives both API-layer normalizers with byte-identical output and
      a frozen shared payload (mutation-freedom contract)
- [x] 4.4 Bridge-level regression: partially duplicated parallel tool uses reach the
      client trimmed; the fast path only frames text that still serializes its payload
- [x] 4.3 Existing bridge streaming, transcript codec, and public contract tests pass
