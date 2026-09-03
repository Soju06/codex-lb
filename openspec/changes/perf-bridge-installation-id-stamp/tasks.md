## 1. Memoize the bridge installation-id stamp

- [x] 1.1 Add `installation_stamp_installation_id`, `installation_stamp_text`
  and `installation_stamp_fresh_text` to `_WebSocketRequestState`.
- [x] 1.2 In `_http_bridge_text_with_account_installation_id`, return the text
  untouched when the installation id matches and the object is one the previous
  call returned (main or fresh); skip the fresh re-stamp and size re-check on
  the same condition; record the returned objects after the size checks pass.

## 2. Splice the first stamp

- [x] 2.1 Add `_splice_account_installation_id`: rewrite the trailing top-level
  `client_metadata` value in place, insert it when the key is absent, and
  return `None` for any other shape so the existing decode/encode path runs.
- [x] 2.2 Route `_text_with_account_installation_id` and the WebSocket
  `_websocket_text_with_account_installation_id` through the splice first.

## 3. Verification

- [x] 3.1 Unit tests: exactly one frame decode across four sequential stamps;
  account swap, id change and text rewrite force a re-stamp; fresh-text memo
  and size check; property test asserting splice == decode/encode output
  byte-for-byte across client_metadata position, id value and value types.
- [x] 3.2 Run `tests/unit/test_proxy_http_bridge.py`, `tests/unit/test_proxy_utils.py`,
  ruff, ty, `scripts/check_proxy_architecture.py` and `openspec validate`.
- [ ] 3.3 Post-deploy: `py-spy --gil` 60 s confirms the helper below 2% of GIL
  samples and count "Waiting for account capacity before retrying HTTP bridge
  submit" log lines per hour to confirm the resubmit-loop hypothesis.
