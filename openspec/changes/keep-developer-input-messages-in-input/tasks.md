# Tasks

## 1. Implementation

- [x] 1.1 `_normalize_responses_input_instructions` forwards `developer`-role messages unchanged and still defaults
  missing `instructions` to `""` for them; only `system`-role messages are hoisted.

## 2. Verification

- [x] 2.1 Unit test `test_responses_input_developer_message_stays_in_input` (Responses and compact, with and without
  `instructions`); updated `test_responses_input_system_message_moves_to_instructions`,
  `test_responses_input_non_message_system_and_developer_items_are_preserved` and
  `test_responses_input_developer_message_keeps_single_non_text_part`, the integration test
  `test_backend_responses_preserves_non_message_developer_directive` and the chat JSON-mode test
  `test_chat_response_format_json_object_preserves_instruction_roles_in_input`. The `chat_responses_shaped` and
  `v1_compact_input` cases of `tests/fixtures/passthrough_request_corpus.json` keep their developer message in `input`.
- [x] 2.2 Live check through a patched 1.24.0 container with the issue #2563 reproduction: both scenarios answer from
  the developer message (about 12.8k input tokens, prompt cache hit), matching a direct upstream connection.
- [x] 2.3 ruff, pytest, strict OpenSpec validation.
