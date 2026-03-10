## Context

`/v1` endpoints are OpenAI-compatibility surfaces, but the implementation translates them onto the ChatGPT Codex backend. That translation currently loses the prompt-cache behavior we can safely preserve: load-balancer affinity only honors `prompt_cache_key` when dashboard sticky-thread mode is enabled, so semantically related OpenAI requests can land on different ChatGPT accounts. `prompt_cache_retention` is documented on the public OpenAI API, but the local Codex references do not show backend support for it.

## Goals / Non-Goals

**Goals:**
- Preserve OpenAI `prompt_cache_key` behavior through Responses payload normalization and routing.
- Route `/v1` prompt-cache requests with deterministic account affinity even when dashboard sticky threads are off.
- Keep backend Codex routing behavior unchanged unless the request enters through the OpenAI-compatibility surface.
- Cover `/v1/responses`, `/v1/responses/compact`, and `/v1/chat/completions` with regression tests.

**Non-Goals:**
- Rework the backend Codex protocol or invent new upstream-only cache metadata.
- Change non-`/v1` dashboard routing policy for callers that use backend Codex endpoints directly.
- Broaden request normalization beyond prompt-cache-related alias handling and the existing unsupported-field list.

## Decisions

### 1. Separate OpenAI cache affinity from dashboard sticky threads

`prompt_cache_key` on `/v1` requests is not just a UI routing hint; it is part of the public API contract. The proxy will therefore treat it as its own affinity mode for OpenAI-facing routes, while keeping `sticky_threads_enabled` as the opt-in behavior for backend Codex routes.

Alternative considered:
- Reuse `sticky_threads_enabled` for `/v1` as-is. Rejected because OpenAI cache correctness would still depend on an unrelated dashboard setting.

### 2. Keep `prompt_cache_retention` compatibility-safe

The shared Responses normalization layer is the single source of truth for all mapped request paths, so any prompt-cache alias handling belongs there. We will normalize `promptCacheRetention` for compatibility with OpenAI-style clients, but continue stripping `prompt_cache_retention` before forwarding because the ChatGPT Codex backend support is not established.

Alternative considered:
- Preserve `prompt_cache_retention` end to end. Rejected because the local Codex references only demonstrate `prompt_cache_key` support, not retention-policy support.

### 3. Normalize prompt-cache aliases at the same payload layer

The existing normalization layer already rewrites OpenAI-compatible alias fields such as `reasoningEffort` and `textVerbosity`. Extending that logic to `promptCacheKey` and `promptCacheRetention` keeps cache-related normalization in one place and avoids silent mismatch for clients that send camelCase.

## Risks / Trade-offs

- [Risk] OpenAI-style clients may send `prompt_cache_retention` expecting backend support. → Mitigation: continue accepting the field for compatibility but strip it before upstream, preserving the behavior codex-lb can actually guarantee.
- [Risk] Always pinning `/v1` prompt-cache traffic may reduce balancing flexibility. → Mitigation: scope the behavior only to requests that explicitly opt in with a non-empty `prompt_cache_key`.
- [Risk] Route-specific affinity flags can drift over time. → Mitigation: thread the new flag explicitly through the API/service boundary and cover all three `/v1` entry points in tests.
