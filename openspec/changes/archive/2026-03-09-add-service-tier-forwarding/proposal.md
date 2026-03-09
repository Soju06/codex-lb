## Why

Codex fast mode is carried over the Responses API as `service_tier: "priority"`. `codex-lb` currently forwards that value only incidentally through permissive request models, which leaves the behavior undocumented and fragile.

## What Changes

- Add explicit `service_tier` support to the shared Responses request models used by `/backend-api/codex/responses`, `/v1/responses`, and Chat Completions mapping.
- Add regression tests that verify `service_tier` survives request normalization and is forwarded upstream.
- Record the forwarding contract in OpenSpec.

## Impact

- Code: `app/core/openai/requests.py`, `app/core/openai/v1_requests.py`, `app/core/openai/chat_requests.py`
- Tests: `tests/unit/test_openai_requests.py`, `tests/unit/test_chat_request_mapping.py`, `tests/integration/test_openai_compat_features.py`
- Specs: `openspec/specs/responses-api-compat/spec.md`, `openspec/specs/chat-completions-compat/spec.md`
