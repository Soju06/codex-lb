## Why

ChatGPT backend rejects audio file inputs (unsupported file type), and codex-lb currently allows chat `input_audio` which fails downstream with inconsistent errors. We also need to align file_id handling with upstream by allowing pass-through instead of local rejection.

## What Changes

- Mark chat `input_audio` as unsupported and return OpenAI invalid_request_error before upstream calls.
- Allow `input_file.file_id` in Responses and Chat mappings to pass through to upstream (no local rejection).
- Update compatibility docs and live checks to reflect audio unsupported + file_id behavior.

## Capabilities

### New Capabilities
- None.

### Modified Capabilities
- `responses-api-compat`: allow `input_file.file_id` to pass through to upstream (no local rejection).
- `chat-completions-compat`: reject `input_audio` as unsupported and allow file_id mapping to pass through.

## Impact

- Code: `app/core/openai/requests.py`, `app/core/openai/chat_requests.py`, `app/core/openai/message_coercion.py`
- Tests: `tests/integration/test_openai_compat_features.py`, `tests/unit/test_openai_requests.py`
- Docs/refs: `refs/openai-compat-test-plan.md`, `scripts/openai_compat_live_check.py`
