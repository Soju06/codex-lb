## Why

Source-routed request logs (`/v1/chat/completions`, `/v1/responses`,
`/backend-api/codex/responses`, `/v1/audio/transcriptions` routed to an
OpenAI-compatible model source) always write `latency_ms` and
`latency_first_token_ms` as `null`. The dashboard's tokens-per-second column
(`output_tokens / (latency_ms - latency_first_token_ms)`) and TTFT column
therefore always render blank for source traffic, even though many
OpenAI-compatible servers (vLLM and its forks in particular) already return
per-request timing in the response body — e.g. a real vLLM response includes:

```
usage: {prompt_tokens: 28, completion_tokens: 9, total_tokens: 37}
metrics: {time_to_first_token_ms: 108.83, generation_time_ms: 162.98,
          queue_time_ms: 0.037, mean_itl_ms: 20.37, tokens_per_second: 33.11}
```

The existing dashboard defines generation speed as `output_tokens` divided by
the time after the first token. Recording `time_to_first_token_ms` as TTFT and
their sum as total latency makes its existing denominator
`latency_ms - latency_first_token_ms` resolve to the upstream-reported
`generation_time_ms`. This preserves the same generation-only TPS semantics
used for subscription-backed requests without instrumenting another proxy-side
timer. An upstream `tokens_per_second` field may use different semantics (for
example, including TTFT) and is therefore not copied directly.

## What Changes

- Parse an optional top-level `metrics` object (`time_to_first_token_ms`,
  `generation_time_ms`) from source chat/responses/audio-transcription JSON
  bodies, and from the final SSE frame of streamed chat/responses (the same
  frame that already carries `usage` when `stream_options.include_usage` is
  set), mirroring the existing usage-parsing pattern.
- Record the parsed timings on the request log as
  `latency_first_token_ms = round(time_to_first_token_ms)` and
  `latency_ms = round(time_to_first_token_ms + generation_time_ms)`, so the
  existing dashboard TTFT and generation-only TPS columns work unchanged for
  source-routed traffic.
- Reject non-finite values (`NaN`, positive infinity, negative infinity) in
  addition to negative or non-numeric timing values, so optional source metrics
  cannot fail an otherwise successful request.
- No new columns, no API schema change: this reuses the existing
  `latency_ms` / `latency_first_token_ms` request-log fields already exposed
  to the dashboard.
- Sources that do not return a `metrics` object are unaffected: both fields
  stay `null`, exactly as before this change.

## Capabilities

### New Capabilities

(none)

### Modified Capabilities

- `proxy-runtime-observability`: source-routed request logs MUST record
  upstream-reported generation timing (TTFT / total generation time) when the
  source provides it, using the same `latency_ms` /
  `latency_first_token_ms` fields as subscription-backed requests.

## Impact

`app/modules/model_sources/forwarding.py` (new `SourceTimings` parsing),
`app/modules/proxy/api.py` (thread timings from forwarding results/stream
holders into `_log_source_chat_completion`). No schema/API/migration change —
timing is written into pre-existing nullable `RequestLog` columns.
