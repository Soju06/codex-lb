# Tasks

- [x] 1. Add OpenSpec requirement for preserving non-message system/developer input items during instruction hoisting.
- [x] 2. Guard the instruction-hoisting normalizer so only message-shaped items are hoisted; keep the Responses Lite `additional_tools` whole-request preservation intact.
- [x] 3. Add regression coverage for `ResponsesRequest` and `ResponsesCompactRequest` with a synthetic non-message item type, asserting preservation through `model_validate` and `to_payload()`.
- [x] 4. Anchor preserved non-message system/developer items in `_trim_compact_input_for_upstream()` so compact trimming does not replace them with the trim marker, with regression coverage on an oversized compact request.
- [x] 5. Treat directive preservation as a normalization change so directive-only requests without top-level `instructions` validate with `instructions` defaulted to `""`, with regression coverage for both request models.
- [x] 6. Validate focused tests and OpenSpec artifacts.
