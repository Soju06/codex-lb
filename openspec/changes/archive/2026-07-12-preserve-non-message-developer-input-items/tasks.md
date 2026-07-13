# Tasks

- [x] 1. Add OpenSpec requirement for preserving non-message system/developer input items during instruction hoisting.
- [x] 2. Guard the instruction-hoisting normalizer so only message-shaped items are hoisted; keep the Responses Lite `additional_tools` whole-request preservation intact.
- [x] 3. Add regression coverage for `ResponsesRequest` and `ResponsesCompactRequest` with a synthetic non-message item type, asserting preservation through `model_validate` and `to_payload()`.
- [x] 4. Anchor preserved non-message system/developer items in `_trim_compact_input_for_upstream()` so compact trimming does not replace them with the trim marker, with regression coverage on an oversized compact request.
- [x] 5. Treat directive preservation as a normalization change so directive-only requests without top-level `instructions` validate with `instructions` defaulted to `""`, with regression coverage for both request models.
- [x] 6. Exempt preserved directives from interleaved-reasoning input sanitization (`_sanitize_input_items()`) via the shared `_is_preserved_non_message_directive()` predicate, so keys like `reasoning_content` and `tool_calls` survive byte-identical, with regression coverage for both request models.
- [x] 7. Add route-level regression coverage through `/backend-api/codex/responses`: a non-message developer directive among messages reaches the captured upstream payload byte-identical while the plain developer message is still hoisted into `instructions` (mirrors the #1161 integration test pattern in `tests/integration/test_proxy_responses.py`).
- [x] 8. Validate focused tests and OpenSpec artifacts.
