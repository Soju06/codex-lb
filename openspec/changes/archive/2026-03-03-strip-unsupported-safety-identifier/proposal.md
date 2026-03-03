## Why

Some clients now send `safety_identifier`, but ChatGPT upstream rejects it on Responses endpoints with `Unsupported parameter: safety_identifier`. This causes avoidable request failures for otherwise valid prompts.

## What Changes

- Strip `safety_identifier` from normalized Responses payloads before upstream forwarding.
- Add regression tests for standard Responses, compact Responses, and Chat-mapped payloads.

## Capabilities

### New Capabilities

- None.

### Modified Capabilities

- `responses-api-compat`: extend unsupported advisory-parameter stripping to include `safety_identifier`.

## Impact

- **Code**: `app/core/openai/requests.py`
- **Tests**: `tests/unit/test_openai_requests.py`, `tests/unit/test_chat_request_mapping.py`
