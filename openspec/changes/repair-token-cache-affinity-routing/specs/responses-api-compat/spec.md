## MODIFIED Requirements

### Requirement: Use prompt_cache_key as OpenAI cache affinity
For OpenAI-style `/v1/responses`, `/v1/responses/compact`, and chat-completions requests mapped onto Responses, the service MUST treat a non-empty `prompt_cache_key` as the bounded upstream account affinity key for prompt-cache correctness even when a `session_id` header is present. OpenAI-style route wiring MUST NOT upgrade those requests to durable `CODEX_SESSION` affinity by default.

When a fresh `prompt_cache` mapping already points at an `openai_platform` routing target, repeated stateless OpenAI-style requests with the same `prompt_cache_key` MUST reuse that Platform routing target within the configured freshness window, even if the ChatGPT-web pool later has healthy compatible candidates again. This reuse is bounded prompt-cache affinity, not durable ChatGPT session continuity.

#### Scenario: OpenAI-style route ignores session header for durable codex-session pinning
- **WHEN** a client sends `/v1/responses` or `/v1/responses/compact` with a non-empty `session_id` header and no explicit sticky-thread mode
- **THEN** the service does not persist a durable `codex_session` mapping solely from that header
- **AND** bounded prompt-cache affinity behavior remains in effect

#### Scenario: Platform fallback prompt-cache hit stays on Platform
- **WHEN** a stateless `/v1/responses` or `/v1/responses/compact` request previously fell back to `openai_platform` and stored a fresh `prompt_cache` mapping
- **AND** a later stateless request uses the same non-empty `prompt_cache_key` within the configured freshness window
- **AND** compatible ChatGPT-web candidates are now healthy
- **THEN** the service reuses the mapped `openai_platform` routing target
- **AND** it does not route the request back to ChatGPT-web solely because the ChatGPT-web pool recovered

### Requirement: Codex backend session_id preserves account affinity
When a backend Codex Responses or compact request includes a non-empty accepted session header, the service MUST use that value as the routing affinity key for upstream ChatGPT-web account selection. If the request lacks a client-supplied `prompt_cache_key`, the service MUST derive and attach a stable `prompt_cache_key` before upstream forwarding so account affinity and upstream prompt-cache routing can coexist. Accepted session headers are `session_id`, `x-codex-session-id`, and `x-codex-conversation-id`, in that priority order.

For Platform fallback selection, the service MUST carry the resolved `prompt_cache_key` as a separate bounded Platform affinity signal even when ChatGPT-web selection uses durable `codex_session` affinity. Platform fallback MUST NOT use the durable backend Codex session key as a Platform `prompt_cache` key.

#### Scenario: Backend Codex request derives prompt_cache_key before codex-session routing
- **WHEN** `/backend-api/codex/responses` is called with `session_id` and without `prompt_cache_key`
- **THEN** the routing decision still uses durable `codex_session` affinity for account selection
- **AND** the forwarded upstream payload includes a derived stable `prompt_cache_key`

#### Scenario: Backend Codex Platform fallback binds resolved prompt_cache_key
- **WHEN** `/backend-api/codex/responses` or `/backend-api/codex/responses/compact` falls back to `openai_platform`
- **AND** the request has both a backend Codex session header and a resolved `prompt_cache_key`
- **THEN** Platform identity selection uses the resolved `prompt_cache_key` as bounded `prompt_cache` affinity
- **AND** it does not persist the backend Codex session header value as an `openai_platform` prompt-cache mapping

### Requirement: Proxy-generated prompt cache key derivation is operator-toggleable
The service MUST provide a runtime flag that disables only proxy-generated prompt-cache-key derivation. When disabled, the service MUST continue forwarding any client-supplied `prompt_cache_key` unchanged and MUST NOT synthesize a new one.

#### Scenario: Derivation disabled preserves client-supplied key
- **WHEN** the derivation flag is disabled and a client sends `prompt_cache_key`
- **THEN** the service forwards that key unchanged
- **AND** it does not generate a replacement key
