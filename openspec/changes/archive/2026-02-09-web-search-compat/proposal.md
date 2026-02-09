## Why

web_search is a core OpenAI built-in tool but is currently blocked by codex-lb validation, causing 422 errors on `/v1/*` and `/backend-api/codex/*`. We need a consistent tool policy that allows web_search across all endpoints while continuing to block other unsupported built-in tools.

## What Changes

- Allow web_search (and its preview alias) in Responses and Chat Completions requests.
- Normalize web_search tool definitions so they pass through the same validation and mapping logic as other tools.
- Continue to reject unsupported built-in tools (file_search, code_interpreter, computer_use, image_generation) for all endpoints with OpenAI error envelopes.

## Capabilities

### New Capabilities
- None.

### Modified Capabilities
- `responses-api-compat`: permit web_search tool types while enforcing the existing unsupported-tool policy for other built-in tools.
- `chat-completions-compat`: allow web_search tools in chat requests and preserve mapping to Responses tools.

## Impact

- Code: `app/core/openai/requests.py`, `app/core/openai/v1_requests.py`, `app/core/openai/chat_requests.py`
- Tests: `tests/integration/test_openai_compat_features.py`
- Docs/refs: `refs/openai-compat-test-plan.md`, `scripts/openai_compat_live_check.py`
