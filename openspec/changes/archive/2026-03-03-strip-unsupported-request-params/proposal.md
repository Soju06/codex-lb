## Why

ChatGPT upstream currently rejects `prompt_cache_retention` and `temperature` on Responses endpoints with `unknown_parameter`, which turns otherwise valid requests into hard failures. We need codex-lb to preserve compatibility by dropping these known unsupported fields before forwarding upstream.

## What Changes

- Strip `prompt_cache_retention` and `temperature` from Responses payloads before upstream forwarding.
- Keep existing pass-through behavior for unrelated extra fields.
- Add regression tests for `/responses`, `/responses/compact`, and Chat->Responses mapping paths.
- Update compatibility support matrix notes for request parameters that are intentionally ignored for upstream compatibility.

## Capabilities

### New Capabilities

- None.

### Modified Capabilities

- `responses-api-compat`: define that known unsupported advisory parameters are removed before upstream forwarding instead of surfacing upstream `unknown_parameter`.

## Impact

- **Code**: `app/core/openai/requests.py`
- **Tests**: `tests/unit/test_openai_requests.py`, `tests/unit/test_chat_request_mapping.py`
- **Docs/Refs**: `refs/openai-compat-test-plan.md`
