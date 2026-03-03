## Why

OpenCode custom provider sessions can emit interleaved reasoning and legacy chat payload fields (for example `input[*].reasoning_content`, `reasoning_details`, `tool_calls`, and `function_call`) that the upstream Codex endpoint does not accept. In practice, this causes runtime failures like:

`Unknown parameter: 'input[1].reasoning_content'`

This blocks prompting for users who enable reasoning controls in OpenCode with `codex-lb`.

## What Changes

- Add request payload sanitization in Responses request normalization to remove unsupported interleaved reasoning and legacy chat fields from `input` items before forwarding upstream.
- Normalize assistant message content parts in `input` so text parts use `output_text` instead of `input_text`, matching upstream role-specific schema constraints.
- Normalize `role: tool` message history to Responses-native `function_call_output` items and reject unsupported/unknown message roles early with clear client errors.
- Preserve top-level `reasoning` request controls (`effort`, `summary`) so reasoning level selection still works.
- Document OpenCode model capability config for `codex-lb` (`reasoning` + `interleaved`) to keep reasoning UI available without relying on implicit defaults.
- Add unit + integration regression tests for payload sanitization behavior.

## Capabilities

### Modified Capabilities

- `responses-api-compat`: sanitize unsupported interleaved reasoning and legacy chat fields in input payloads while preserving supported reasoning controls.

## Impact

- **Code**: `app/core/openai/requests.py`, `app/core/openai/message_coercion.py`, `app/core/openai/chat_requests.py`
- **Tests**: `tests/unit/test_openai_requests.py`, `tests/unit/test_chat_request_mapping.py`, `tests/integration/test_proxy_responses.py`
- **Docs**: `README.md` (OpenCode configuration examples)
