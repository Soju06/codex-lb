## 1. Retire remembered-stale session anchors

- [x] 1.1 Add `_retire_websocket_continuity_anchor` in
  `app/modules/proxy/_service/websocket/helpers.py` clearing the completed
  response id, stored input count, prefix fingerprint, and pending tool-call
  metadata; reuse it from `_record_websocket_continuity_completion(response_id=None)`.
- [x] 1.2 In `_websocket_continuity_anchor_for_payload`, accept `api_key_id`
  and, before the stored-prefix comparison, consult
  `_is_websocket_stale_previous_response`; on a hit retire the anchor, log
  `websocket_session_anchor_retired … reason=stale_previous_response`, and
  return no anchor.
- [x] 1.3 In `_prepare_websocket_response_create_request`
  (`app/modules/proxy/_service/websocket/mixin.py`), pass the refreshed API key
  id so the lookup key matches the one the fail-closed path remembered.

## 2. Regression coverage

- [x] 2.1 `tests/unit/test_proxy_utils.py::test_prepare_websocket_response_create_request_retires_injected_anchor_remembered_stale`:
  remember a denied anchor for the key, send the follow-up full-context frame,
  and assert the upstream payload has no `previous_response_id`, the input is
  untrimmed, the request state is not marked proxy-injected, and continuity no
  longer holds the anchor.

## 3. Verification

- [x] 3.1 `uv run pytest tests/unit/test_proxy_utils.py`.
- [x] 3.2 `ruff check` / `ruff format --check` on changed files, `ty check`,
  and the proxy architecture / cancellation-safety / timing-seam /
  settings-tier scripts.
- [x] 3.3 `openspec validate retire-stale-websocket-session-anchor-before-reinjection --strict`
  and `openspec validate --specs`.
