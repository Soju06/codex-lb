# Tasks: record-resolved-upstream-transport-label

## 1. Implementation

- [x] 1.1 `app/modules/proxy/_service/streaming/retry.py` `_stream_with_retry`:
  preserve the client mode `"auto"` for 426 fallback; use the resolved
  `"http"`/`"websocket"` transport in every raw request-log write and in
  `_record_upstream_transport_decision`.
- [x] 1.2 `app/modules/proxy/_service/streaming/mixin.py` `_stream_once`:
  persist the resolved label passed from retry, independently of client mode.

## 2. Regression coverage

- [x] 2.1 Add `tests/integration/test_proxy_responses.py::test_bypassed_request_logs_resolved_upstream_transport`
  (real `/v1/responses` route, bridge-enabled inline-image bypass,
  `always_websocket`). Assert the persisted row and counter label are
  `"websocket"`, but the upstream client still receives `"auto"`.
- [x] 2.2 Include a sticky smart-policy raw case with the same persisted
  `"websocket"` label and `"auto"` client mode.
- [x] 2.3 Focused label smoke passed (bridge-image bypass and sticky-raw cases).
  No commit or push in this assignment. The pre-edit run was blocked by
  missing test dependencies; see `context.md`.

## 3. Spec hygiene

- [ ] 3.1 On sync, replace the scenario in place in
  `openspec/specs/responses-api-compat/spec.md`. Do not append a dated
  correction. (Sync is outside the owned paths for this assignment.)
