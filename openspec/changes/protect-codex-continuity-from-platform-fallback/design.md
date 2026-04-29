## Context

Backend Codex HTTP fallback intentionally treats downstream session headers as transport hints so stateless HTTP requests can use OpenAI Platform when ChatGPT Web usage is drained. That is correct for unowned or fresh requests, but it is too aggressive for requests whose `x-codex-turn-state` or session header already maps to a ChatGPT Web `codex_session`. Moving those requests to Platform skips the ChatGPT HTTP bridge input-prefix trim path and causes full-input Platform billing.

## Goals / Non-Goals

**Goals:**

- Keep Platform fallback available for backend Codex HTTP when there is no existing ChatGPT continuity owner.
- Keep a backend Codex request on ChatGPT Web while its `codex_session` owner is still selectable, even if that owner is above usage-drain thresholds.
- Preserve emergency fallback when the sticky owner is not selectable or `platform_fallback_force_enabled` is enabled.

**Non-Goals:**

- Implement Platform-side `previous_response_id` continuity for backend Codex.
- Change prompt-cache-key derivation or Platform prompt cache retention.
- Disable Platform fallback globally for backend Codex HTTP.

## Decisions

- Add a non-blocking request capability hint for backend Codex session headers. This differs from `continuity_param`: it does not make Platform unsupported by capability check, but it lets provider selection protect an existing ChatGPT continuity owner.
- Add a sticky-owner selectable check separate from the existing Platform-fallback health check. The existing health check includes remaining-budget thresholds; continuity protection should ignore those thresholds and care only whether the pinned ChatGPT target can still serve the request.
- Apply the selectable-owner guard only when fallback is usage-drain driven, the request has hard `codex_session` affinity, the backend Codex session-header hint is present, and force fallback is disabled.
- Disable sticky budget-pressure reallocation for backend Codex `x-codex-turn-state` streaming affinity. This preserves the same ChatGPT owner, not only the same provider, while leaving the existing budget reallocation behavior enabled for explicit session-header and compact sticky flows.

## Risks / Trade-offs

- [More ChatGPT usage near quota] -> Only owned continuity sessions are protected; unowned backend Codex requests can still drain to Platform, and explicit session-header compact flows retain existing sticky budget reallocation.
- [Sticky owner may hit an upstream limit sooner] -> The guard only applies while the owner remains selectable; rate-limited, cooled-down, paused, or deactivated owners still allow fallback.
- [Compact routes share backend Codex route family] -> The guard is capability-based and hard-affinity-based, matching existing compact sticky behavior that already preserves grace-eligible sticky sessions.
