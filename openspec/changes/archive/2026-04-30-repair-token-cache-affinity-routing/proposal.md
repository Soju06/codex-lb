# repair-token-cache-affinity-routing

## Why
OpenAI-style `/v1/*` routes currently enable `codex_session_affinity=True`, which can convert bounded prompt-cache routing into durable `CODEX_SESSION` pinning whenever a `session_id` header is present. Separately, backend Codex routes can early-return on `CODEX_SESSION` before a proxy-derived `prompt_cache_key` is attached, which weakens upstream prompt-cache routing hints when clients omit that field. Operators also lack route-level affinity traces to distinguish session-driven account affinity from prompt-cache routing.

## What Changes
- Restore OpenAI-style `/v1/*` routes to bounded `prompt_cache_key` affinity by default.
- Keep backend Codex `session_id` account affinity, but ensure prompt-cache-key derivation happens before `CODEX_SESSION` early return when needed.
- Harden accepted session header aliases.
- Add request-shape observability for sticky kind/source and prompt-cache-key injection.
- Add a feature flag to disable proxy-generated prompt-cache-key derivation for A/B testing.

## Impact
- `/v1/responses`, `/v1/responses/compact`, `/v1/chat/completions`, and `/v1/responses` websocket return to prompt-cache-bounded routing semantics.
- Backend Codex keeps durable account affinity but can still send a stable upstream prompt-cache hint.
- Operators gain safer diagnostics for routing decisions.
