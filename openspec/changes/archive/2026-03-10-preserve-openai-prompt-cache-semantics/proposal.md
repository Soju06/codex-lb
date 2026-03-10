## Why

`/v1` compatibility currently breaks the part of OpenAI-style prompt caching semantics that codex-lb can safely preserve against the ChatGPT Codex backend. The proxy treats `prompt_cache_key` as optional dashboard stickiness instead of required cache affinity, so repeated cache-keyed requests can land on different upstream accounts.

## What Changes

- Treat `prompt_cache_key` on OpenAI-style `/v1` requests as upstream account affinity so repeated cache-keyed requests stay on the same ChatGPT account even when dashboard sticky threads are disabled.
- Normalize `promptCacheKey` and `promptCacheRetention` aliases for compatibility, while continuing to strip `prompt_cache_retention` before the ChatGPT Codex backend because backend support is not established.
- Preserve the same `prompt_cache_key` behavior through `/v1/responses`, `/v1/responses/compact`, and `/v1/chat/completions` mapping paths.
- Add regression tests for payload normalization, `/v1` routing affinity, and Chat Completions mapping.

## Capabilities

### New Capabilities

- None.

### Modified Capabilities

- `responses-api-compat`: preserve OpenAI prompt cache controls across `/v1` translation and route `prompt_cache_key` requests with cache affinity.
- `chat-completions-compat`: preserve prompt cache controls when Chat Completions requests are mapped onto the internal Responses wire format.

## Impact

- **Code**: `app/core/openai/requests.py`, `app/modules/proxy/api.py`, `app/modules/proxy/service.py`
- **Tests**: `tests/unit/test_openai_requests.py`, `tests/integration/test_proxy_sticky_sessions.py`, `tests/integration/test_openai_compat_features.py`
- **Behavior**: `/v1` prompt-cache requests now preserve cache metadata and reuse the same upstream account independent of dashboard sticky-thread settings
