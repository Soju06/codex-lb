# Tasks

## Implementation

- [x] T1: Rewrite `_normalize_message_content` in `app/core/openai/message_coercion.py` to emit `{role, content}` explicitly instead of copying the inbound message dict.
- [x] T2: Add unit test `test_chat_unknown_message_keys_are_dropped` covering the original `_empty_recovery_synthetic` reproducer.
- [x] T3: Add unit test `test_chat_message_name_field_is_dropped` covering the standard `name` field.
- [x] T4: Add unit test `test_chat_assistant_tool_call_message_drops_unknown_keys` covering the assistant + tool_calls path.

## Spec

- [x] T5: Update `openspec/specs/chat-completions-compat/spec.md` with the new "Drop unknown message-object fields" Requirement and scenarios (applied via the delta in `openspec/changes/drop-unknown-message-fields/specs/chat-completions-compat/spec.md`).

## Validation

- [x] T6: Run targeted suites — `tests/unit/test_chat_request_mapping.py`, `tests/unit/test_openai_requests.py`, `tests/integration/test_proxy_chat_completions.py`, `tests/integration/test_openai_client_compat.py`, `tests/integration/test_openai_compat_features.py` — all green (187 passed).
- [x] T7: Run broader related sweep — `tests/unit/`, `tests/integration/test_proxy_responses.py`, `tests/e2e/test_proxy_flow.py` filtered to coercion/chat/responses/proxy — 633 passed, 0 failures introduced.
- [x] T8: Re-run the original reproducer (the exact payload that produced `502 unknown_parameter 'input[N]._empty_recovery_synthetic'`) and confirm coerced `input` items now carry only `role` + `content`.
