## 1. Responses tool compatibility

- [ ] 1.1 Split `UNSUPPORTED_TOOL_TYPES` into `RESPONSES_UNSUPPORTED_TOOL_TYPES` (empty) and `CHAT_UNSUPPORTED_TOOL_TYPES` (existing list) in `app/core/openai/requests.py`.
- [ ] 1.2 Parameterize `validate_tool_types` with a keyword-only `unsupported_tool_types` defaulting to `RESPONSES_UNSUPPORTED_TOOL_TYPES`; keep alias normalization unchanged.
- [ ] 1.3 Update `ChatCompletionsRequest._validate_tools` to pass `CHAT_UNSUPPORTED_TOOL_TYPES` so `/v1/chat/completions` keeps its stricter policy.
- [ ] 1.4 Leave `V1ResponsesRequest._validate_tools` and `ResponsesRequest._validate_tools` on the default argument so Responses-family payloads forward built-ins unchanged.

## 2. Test coverage

- [ ] 2.1 Flip `tests/unit/test_openai_requests.py::test_v1_rejects_builtin_tools` to assert the built-in tool is accepted and preserved on the Responses request.
- [ ] 2.2 Replace `test_v1_responses_rejects_builtin_tools` in `tests/integration/test_openai_compat_features.py` with an acceptance test that covers `/v1/responses` for every previously rejected built-in and asserts the tool reaches upstream unchanged.
- [ ] 2.3 Add a parallel acceptance test for `/backend-api/codex/responses` with the same built-in tool payloads.
- [ ] 2.4 Extend the WebSocket Responses test in `tests/integration/test_proxy_websocket_responses.py` so a `response.create` payload carrying multiple built-in tools is forwarded upstream unchanged.
- [ ] 2.5 Keep `test_v1_chat_completions_rejects_builtin_tools` unchanged to lock in Chat Completions strict behavior.

## 3. Validation

- [ ] 3.1 `openspec validate allow-responses-built-in-tools --strict`.
- [ ] 3.2 `.venv/bin/python -m pytest tests/unit/test_openai_requests.py tests/integration/test_openai_compat_features.py tests/integration/test_proxy_websocket_responses.py`.
- [ ] 3.3 `.venv/bin/ruff check app tests` and `.venv/bin/ruff format --check app tests`.
- [ ] 3.4 `.venv/bin/ty check app`.
