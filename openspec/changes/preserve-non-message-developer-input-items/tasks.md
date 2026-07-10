# Tasks

- [x] 1. Add OpenSpec requirement for preserving non-message system/developer input items during instruction hoisting.
- [x] 2. Guard the instruction-hoisting normalizer so only message-shaped items are hoisted; keep the Responses Lite `additional_tools` whole-request preservation intact.
- [x] 3. Add regression coverage for `ResponsesRequest` and `ResponsesCompactRequest` with a synthetic non-message item type, asserting preservation through `model_validate` and `to_payload()`.
- [x] 4. Validate focused tests and OpenSpec artifacts.
