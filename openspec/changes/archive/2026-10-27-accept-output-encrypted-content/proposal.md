## Why

Codex clients can request `output.encrypted_content` alongside `reasoning.encrypted_content`. The Responses include allowlist rejects the former before upstream dispatch, so otherwise valid chat requests fail with `invalid_request_error` on `include`.

## What Changes

- Accept and forward `output.encrypted_content` through native and OpenAI-compatible Responses requests.
- Preserve existing include validation and opaque output replay behavior.
- Add ordinary request, HTTP, WebSocket, SDK and replay-safety regression coverage.
- Document the supported selector and its opaque-state contract in `responses-api-compat`.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `responses-api-compat`: accept the encrypted-output include selector and preserve encrypted item state across Responses transport and replay.

## Impact

One allowlist entry in `app/core/openai/requests.py`, existing test suites, shared synthetic test data, and OpenSpec. No settings, dependencies, schema migrations, routing-policy changes or deployment changes.
