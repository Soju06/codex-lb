## Why

Backend Codex routes use durable `codex_session` stickiness when the client sends a `session_id` header. Today that stickiness preserves continuity too aggressively: if the pinned account remains `ACTIVE` but its short-window usage is already above the sticky reallocation threshold, selection still keeps routing the session there until upstream starts returning `usage_limit_reached`.

Separately, fresh selection still lets near-exhausted active accounts compete with budget-safe accounts until a hard failure occurs. In production this surfaces as repeated compact and websocket failures on accounts whose latest local primary-window usage is already in the `97-99%` range, even while other active accounts still have healthy short-window budget.

## What Changes

- extend proactive sticky reallocation to durable backend `codex_session` mappings when the pinned account is above the configured budget threshold and a healthier candidate exists
- prefer budget-safe Responses routing candidates over already-pressured candidates whenever at least one budget-safe candidate exists
- keep existing durable `codex_session` behavior below that threshold
- add regression coverage for backend Codex responses + compact routing with `session_id`

## Impact

- backend Codex sessions may rebind to a different account slightly earlier, before the pinned account hard-fails upstream on short-window budget exhaustion
- fresh Responses requests will prefer accounts that are still below the configured budget threshold instead of spending more attempts on near-exhausted accounts first
- OpenAI `/v1` routes still do not create durable `codex_session` mappings from `session_id`, but they do benefit from the same budget-safe fresh-selection preference
