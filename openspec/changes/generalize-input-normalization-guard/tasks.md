# Tasks

- [x] 1. Add OpenSpec requirement that only message-shaped instruction items are hoisted during input normalization.
- [x] 2. Guard the hoisting loop in `_normalize_responses_input_instructions` by item type, keeping the #1161 Lite early return intact.
- [x] 3. Add regression coverage with a synthetic non-message developer item for `ResponsesRequest` and `ResponsesCompactRequest`.
- [x] 4. Validate focused tests and OpenSpec artifacts.
