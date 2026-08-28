## 1. Implementation

- [x] 1.1 `SourceTimings` dataclass + `_timings_from_metrics`/`_timings_from_payload` parsers in `app/modules/model_sources/forwarding.py`
- [x] 1.2 Thread timings through `SourceChatCompletion`/`SourceResponsesCompletion`/`SourceAudioTranscription` (non-stream) and `SourceUsageHolder` (stream, captured from the final SSE frame alongside `usage`)
- [x] 1.3 `_log_source_chat_completion` accepts `timings` and maps it onto `latency_ms`/`latency_first_token_ms` at all 5 success call sites (chat/responses non-stream and stream, audio transcription)

## 2. Validation

- [x] 2.1 Unit tests: metrics parsing (top-level + nested `response.metrics`), negative/missing/non-finite rejection, dashboard generation-only TPS semantics, SSE final-frame capture for both chat and responses shapes
- [x] 2.2 Integration test: a source response carrying `metrics` lands on `RequestLog.latency_first_token_ms`/`latency_ms`
- [x] 2.3 Full model-sources suite green; `ruff`/`ty`; `openspec validate --specs`
