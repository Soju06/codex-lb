# Tasks — validate-stream-lifecycle-events-only

## 1. Implementation

- [x] 1.1 `_LIFECYCLE_EVENT_TYPES` + `classify_event_type` in
      `app/core/openai/parsing.py`; `_event_type_from_payload` and
      `tool_call_dedupe.event_type_from_payload` delegate to it
- [x] 1.2 Streaming mixin (first-event block + loop): classify from dict,
      validate lifecycle frames only
- [x] 1.3 Core client: delete redundant per-chunk `parse_sse_event` terminal
      checks (SSE loops keep the `normalized_event_type` branch); websocket
      receive loops detect terminal via `parse_sse_data_json` + `type`
- [x] 1.4 Websocket relay: single `json.loads`, lifecycle-only validation,
      pass `event=` into `rewrite_parallel_tool_call_text`, stop building the
      discarded `format_sse_event` argument, skip `json.dumps` on identity
      response-id rewrite
- [x] 1.5 `tool_call_dedupe` rewrite helpers: no re-validation on the
      unchanged path
- [x] 1.6 HTTP-bridge upstream reader: same lifecycle gating

## 2. Validation

- [x] 2.1 Existing streaming/websocket/bridge unit + integration suites pass
      unmodified (byte-level SSE assertions act as the parity oracle)
- [x] 2.2 Regression tests: lifecycle-only validation counts with interleaved
      `event=None` deltas on the SSE mixin and websocket relay (usage
      settlement, error rewrite, response-id assignment), dedupe helpers do
      not re-validate unchanged frames, `classify_event_type` unit coverage
- [x] 2.3 `uvx ruff format --check`, `uv run ruff check` on changed files
