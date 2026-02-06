## Why

codex-lb rotates upstream accounts and does not provide a file upload endpoint, so `file_id` inputs cannot be resolved reliably. Allowing them produces inconsistent behavior and breaks expectations; we need to reject `file_id` early and align error messages with upstream invalid request wording.

## What Changes

- **BREAKING**: Reject `input_file.file_id` in `/v1/responses` with an OpenAI invalid_request_error.
- **BREAKING**: Reject chat `file` content parts that include `file_id` in `/v1/chat/completions` with an OpenAI invalid_request_error.
- Normalize local error messages to match upstream wording for these rejections.

## Capabilities

### New Capabilities
- `none`: None.

### Modified Capabilities
- `responses-api-compat`: Reject `input_file.file_id` and standardize invalid_request_error message.
- `chat-completions-compat`: Reject chat `file_id` content parts and standardize invalid_request_error message.

## Impact

- Request validation and message coercion in `app/core/openai/*`.
- Error envelope mapping for invalid request payloads.
- Integration/unit tests and compatibility documentation.
