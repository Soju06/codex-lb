## 1. OpenSpec

- [x] Add `responses-api-compat` delta describing raw upstream SSE error preservation when
  `enforce_openai_sdk_contract=False` on `/backend-api/codex/responses`.

## 2. Code

- [x] Thread `enforce_openai_sdk_contract` through proxy API/service/core stream forwarding for HTTP transport.
- [x] Preserve raw upstream SSE event blocks in `_normalize_stream_payload_for_http_block`
  when `enforce_openai_sdk_contract=False`.

## 3. Tests

- [x] Add regression test in `tests/unit/test_proxy_utils.py` proving raw error-event shape is preserved
  in HTTP streaming when contract enforcement is disabled.
