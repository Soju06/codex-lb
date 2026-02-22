# Anthropic / Claude Code Compatible Endpoint Plan

## Status
- OpenSpec workflow is intentionally skipped for this task by explicit request.

## Goal
- Add a separate Anthropic-compatible API surface for Claude Code.
- Keep existing OpenAI-compatible routes unchanged.
- Provide token counting without adding a local tokenizer.

## Hard Constraints
- New routes must not conflict with current routes.
- New implementation must not change behavior of:
  - `/v1/responses`
  - `/v1/chat/completions`
  - `/backend-api/codex/*`
- No local tokenizer in this phase.

## New Public Endpoints
- `POST /v1/messages`
- `POST /anthropic/v1/messages` (alias)
- `POST /v1/messages/count_tokens`
- `POST /anthropic/v1/messages/count_tokens` (alias)
- `POST /api/event_logging/batch`
- `POST /anthropic/api/event_logging/batch` (alias)

## Endpoint Behavior

### 1) Messages create
- Accept Anthropic-style request payload (`model`, `messages`, optional `system`, `tools`, `tool_choice`, `stream`, etc.).
- Translate request into internal `ResponsesRequest` representation.
- Reuse existing proxy pipeline (`ProxyService`) for account routing, retries, and upstream calls.
- Return Anthropic-style response for non-streaming.
- Return Anthropic-style SSE events for streaming.

### 2) Count tokens (no tokenizer)
- Implement by upstream probe, not local tokenization.
- Flow:
  - Translate Anthropic input to internal request shape.
  - Execute a minimal upstream call through existing proxy service.
  - Extract `usage.input_tokens`.
  - Return `{ "input_tokens": <int> }`.
- If usage is missing or invalid, return Anthropic-style API error (5xx).

### 3) Event logging batch
- Stub endpoint for Claude Code telemetry compatibility.
- Always return `{ "status": "ok" }`.

## Isolation Strategy
- Implement in a dedicated module tree:
  - `app/modules/anthropic_compat/api.py`
  - `app/modules/anthropic_compat/service.py`
  - `app/modules/anthropic_compat/schemas.py`
  - `app/modules/anthropic_compat/translator.py`
- Add a dedicated context provider in `app/dependencies.py`.
- Register routers in `app/main.py` only (no modifications to existing proxy route contracts).

## Auth Strategy
- Add dedicated auth dependency for anthropic-compatible routes.
- Support both header styles:
  - `x-api-key`
  - `Authorization: Bearer ...`
- Reuse current API key validation service (`ApiKeysService`).
- Do not alter existing `validate_proxy_api_key` behavior.

## Error Contract
- Anthropic-compatible routes return Anthropic-style error envelopes.
- Existing routes keep current OpenAI-style error envelopes.
- Error format separation is strict by router.

## Mapping Rules
- `system` and text instructions are merged into internal `instructions`.
- `messages` are converted into internal `input` items.
- Tool calls and tool outputs are mapped to internal function-call compatible structures.
- Unsupported payload combinations fail fast with clear client errors.
- Request validation is tolerant to Claude Code style metadata on blocks (e.g. `cache_control`).
- `messages` with role `system` are accepted and merged into instructions.
- For `claude-*` requested models, the anthropic-compatible layer remaps to an available upstream model from allowed/registered OpenAI models.
- For Claude Code requests (`claude-*`), the anthropic-compatible layer now enforces:
  - upstream model: `gpt-5.3-codex`
  - reasoning effort: `xhigh`
  - sampling controls `temperature/top_p/top_k` are stripped for compatibility with forced codex routing.
  - `prompt_cache_key` resolution for sticky/prompt caching:
    - explicit top-level `prompt_cache_key` / `promptCacheKey`
    - fallback to `metadata` (`conversation_id`/`thread_id`/`session_id`, etc.)
    - fallback to deterministic hash derived from Anthropic `cache_control` blocks
    - final fallback to deterministic conversation anchor hash (system + first user + tools) when `cache_control` is absent

## Token Counting Notes
- This is intentionally not a local deterministic tokenization path.
- Accuracy depends on upstream usage reporting.
- Latency and account usage impact are expected tradeoffs in this phase.

## Non-Impact Guarantees
- No DB migration required.
- No changes to existing OpenAI endpoint schemas.
- No changes to existing OpenAI endpoint URLs.

## Test Plan

### Unit
- Anthropic request translation.
- Anthropic response translation.
- Count-tokens probe logic and error branches.

### Integration
- `POST /v1/messages` non-streaming happy path.
- `POST /v1/messages` streaming happy path.
- `POST /v1/messages/count_tokens` happy path.
- `POST /v1/messages/count_tokens` missing usage path (error).
- Route isolation regression:
  - Existing `/v1/responses` behavior unchanged.
  - Existing `/v1/chat/completions` behavior unchanged.

## Done Criteria
- All new unit/integration tests pass.
- Existing compatibility tests remain green.
- New routes are isolated and do not alter old route behavior.

## Implementation Status (2026-02-21)
- Implemented dedicated module:
  - `app/modules/anthropic_compat/api.py`
  - `app/modules/anthropic_compat/service.py`
  - `app/modules/anthropic_compat/schemas.py`
  - `app/modules/anthropic_compat/translator.py`
- Registered routers in `app/main.py`:
  - `POST /v1/messages`
  - `POST /anthropic/v1/messages`
  - `POST /v1/messages/count_tokens`
  - `POST /anthropic/v1/messages/count_tokens`
  - `POST /api/event_logging/batch`
  - `POST /anthropic/api/event_logging/batch`
- Added dedicated DI context in `app/dependencies.py`:
  - `AnthropicCompatContext`
  - `get_anthropic_compat_context()`
- Added dedicated auth + format marker in `app/core/auth/dependencies.py`:
  - `set_anthropic_error_format()`
  - `validate_anthropic_api_key()` with support for `x-api-key` and `Authorization: Bearer ...`
- Added Anthropic envelope support in error stack:
  - `app/core/errors.py` (`anthropic_error`)
  - `app/core/handlers/exceptions.py` (`anthropic` format branch)
- Kept existing routes untouched by contract:
  - `/v1/responses`
  - `/v1/chat/completions`
  - `/backend-api/codex/*`
- Added tests:
  - `tests/integration/test_anthropic_compat.py`
  - `tests/unit/test_anthropic_translator.py`
- Added Claude Code compatibility hardening:
  - Request block schemas switched to `extra="allow"` for `messages`/`system` blocks.
  - `AnthropicMessage.role` accepts `system`.
  - Translator accepts metadata-bearing blocks and preserves compatibility with `tool_use`/`tool_result` validation.
  - Anthropic `claude-*` model names are remapped to an available OpenAI model for account selection, while Anthropic response `model` remains the requested value.
  - Claude requests are hard-forced to `gpt-5.3-codex` with reasoning effort `xhigh` before proxy routing.
  - Claude requests now drop upstream-unsupported sampling fields (`temperature`, `top_p`, `top_k`) to prevent 400 errors like `Unsupported parameter: temperature`.
  - Claude requests now populate `prompt_cache_key` so sticky account routing and prompt cache hits work in Anthropic-compatible flow.
  - Added non-`cache_control` anchor fallback to keep prompt cache key stable across turns in Claude Code flows that omit Anthropic cache markers.
  - Added Claude shared-cache policy to reduce cache misses in multi-call Claude flows:
    - for `claude-*` requests, `cache_control`-derived keys are preserved
    - all other key sources (`explicit`, `metadata`, `anchor`, `none`) are replaced with a deterministic `claude-shared:*` key derived from API key + requested Claude model + system + tools
    - this intentionally stabilizes cache key across sessions for the same harness/profile.
  - Added Anthropic prompt cache diagnostics log line:
    - event name: `anthropic_prompt_cache`
    - fields: `request_id`, `operation`, `requested_model`, `upstream_model`, `source` (`explicit|metadata|cache_control|anchor|none`), hashed cache key
    - raw cache key output remains guarded by `CODEX_LB_LOG_PROXY_REQUEST_SHAPE_RAW_CACHE_KEY=true`.
  - Added request-log correlation hashes for Codex headers:
    - DB columns in `request_logs`: `codex_session_hash`, `codex_conversation_hash`
    - Values are stored as non-reversible short SHA-256 fingerprints of inbound headers:
      - `x-codex-session-id`
      - `x-codex-conversation-id`
    - Exposed via `/api/request-logs` response fields:
      - `codexSessionHash`
      - `codexConversationHash`

## Validation Results
- `uv run ruff check ...` for all touched files: passed
- `uv run pytest tests/unit/test_anthropic_translator.py tests/integration/test_anthropic_compat.py -q`: passed (`17 passed`)
- `uv run pytest tests/integration/test_proxy_responses.py tests/integration/test_auth_middleware.py tests/integration/test_openai_client_compat.py -q`: passed (`36 passed`)
