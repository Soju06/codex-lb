## Why

Codex-lb currently diverges from OpenAI client expectations for multimodal inputs and unsupported features, leading to confusing upstream errors. We need to normalize inputs, explicitly reject unsupported fields/tools, and document support to make compatibility behavior predictable.

## What Changes

- Normalize `/v1/responses` string `input` into list-based `input_text` items for upstream compatibility.
- Convert Chat Completions multimodal content parts (`text`, `image_url`, `input_audio`, `file`) into Responses-compatible input parts.
- Best-effort proxy of `input_image` URLs into data URLs to avoid upstream download failures.
- Explicitly reject unsupported fields and tools: `file_input_id`, `previous_response_id`, `truncation`, and built-in tools (web/file search, code interpreter, computer use, image generation).
- Extend compatibility documentation with a clear support matrix and update live check script to print expected support vs. unsupported features.

## Capabilities

### New Capabilities
- `compatibility-tooling`: Define and maintain the support matrix and live compatibility checks for OpenAI client behavior.

### Modified Capabilities
- `responses-api-compat`: Update request normalization and unsupported-field behavior for `/v1/responses`.
- `chat-completions-compat`: Update multimodal mapping rules from Chat inputs to Responses inputs.

## Impact

- Code: `app/core/openai/requests.py`, `app/core/openai/v1_requests.py`, `app/core/openai/chat_requests.py`, `app/core/openai/message_coercion.py`, `app/core/clients/proxy.py`, `app/modules/proxy/api.py`
- Tests: `tests/integration/test_openai_compat_features.py`
- Docs/refs: `refs/openai-compat-test-plan.md`
- Scripts: `scripts/openai_compat_live_check.py`
