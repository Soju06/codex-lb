## 1. Implementation

- [x] 1.1 Drop Codex-internal (`codex.`-prefixed) events in `_normalize_public_stream_payload` so they never reach the `/v1` public stream; leave `/backend-api/codex/*` forwarding untouched
- [x] 1.2 Track `response.output_item.done` events in `_normalize_public_responses_stream` and backfill `response.completed` / `response.incomplete` `output` from collected items when the terminal payload's `output` is empty or missing, reusing the existing `_merge_collected_output_items` / `_normalize_public_response_mapping` helpers
- [x] 1.3 Synthesize a `response.created` SSE event from the current event's `response` envelope when the first standard event the public stream would emit is not `response.created` (e.g. upstream-rejection error streams that jump straight to `response.failed`)

## 2. Tests

- [ ] 2.1 Unit: extend `tests/unit/test_proxy_api_responses_contract.py` — `_normalize_public_responses_stream` drops a leading `codex.rate_limits` event and yields `response.created` as the first event
- [ ] 2.2 Unit: `_normalize_public_responses_stream` backfills terminal `output` from streamed `response.output_item.done` events when `response.completed` carries `output: []`
- [ ] 2.3 Unit: `_normalize_public_responses_stream` synthesizes `response.created` when the upstream stream's first standard event is `response.failed` (or any other non-created event)
- [ ] 2.4 E2E: add `tests/e2e/test_v1_responses_openai_sdk.py` driving the real `openai` SDK (`responses.stream` + `responses.create`) through the in-process ASGI app for plain-text, tool-call, structured-output, and error-stream shapes; assert the SDK stream parser does not raise and `get_final_response().output` is populated

## 3. Spec Delta

- [ ] 3.1 Add a `responses-api-compat` spec delta for the public `/v1` streaming SSE contract (drop Codex-internal events, backfill terminal output, synthesize leading `response.created`)
- [ ] 3.2 Validate specs locally with `openspec validate normalize-v1-responses-openai-sdk-stream --strict`
