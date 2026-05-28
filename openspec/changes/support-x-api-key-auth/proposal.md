# Support x-api-key Authentication

## Why

Some OpenAI-compatible clients and reverse proxies send API keys through `x-api-key` instead of `Authorization: Bearer ...`. `codex-lb` currently accepts only Bearer tokens for its API-key-authenticated proxy surfaces, which forces those clients to be reconfigured even though the same key material is available in a standard header.

## What Changes

- Accept `x-api-key: sk-clb-...` anywhere codex-lb currently accepts its own Bearer API key.
- Keep existing Bearer behavior unchanged.
- When both `Authorization` and `x-api-key` are present, prefer `Authorization` first and fall back to `x-api-key` only if the Authorization value is missing, malformed, or invalid.
- Keep ChatGPT caller-identity flows Bearer-only; `x-api-key` applies only to codex-lb API key authentication.

## Impact

- **Code**: `app/core/auth/dependencies.py`, proxy auth call sites, and focused auth tests.
- **Behavior**: proxy/API-key clients may authenticate with either `Authorization: Bearer ...` or `x-api-key: ...`.
- **Non-goals**: no dashboard auth changes, no custom support for `api-key`/`openai-api-key`, and no change to upstream ChatGPT token validation semantics.
