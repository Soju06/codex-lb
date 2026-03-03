## 1. Payload Sanitization

- [x] 1.1 Add Responses payload sanitizer to strip interleaved reasoning and legacy chat input fields (`reasoning_content`, `reasoning_details`, `tool_calls`, `function_call`) before upstream forwarding
- [x] 1.2 Ensure unsupported reasoning-only content parts are removed from `input` arrays
- [x] 1.3 Preserve top-level `reasoning` controls (`effort`, `summary`) unchanged
- [x] 1.4 Normalize assistant text content parts from `input_text` to `output_text` for assistant-role input messages
- [x] 1.5 Normalize `role: tool` messages into `function_call_output` items and validate supported message roles

## 2. Documentation

- [x] 2.1 Update README OpenCode `codex-lb` examples to include explicit model capability flags for reasoning (`reasoning`, `interleaved`)

## 3. Tests

- [x] 3.1 Add unit tests for input sanitization and top-level reasoning preservation
- [x] 3.2 Add integration regression test to confirm sanitized payload is forwarded in `/v1/responses`
- [x] 3.3 Add role/tool-message regression tests for `/v1/responses` and chat-request mapping

## 4. Spec Delta

- [x] 4.1 Add `responses-api-compat` requirement delta for interleaved reasoning and legacy chat field sanitization
- [ ] 4.2 Validate specs locally with `openspec validate --specs` (CLI not available in this environment)
