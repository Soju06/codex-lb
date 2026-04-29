## Why

Backend Codex HTTP requests can fall back from ChatGPT Web to OpenAI Platform when the ChatGPT pool is usage-drained. For requests that already carry a ChatGPT `codex_session` affinity key, that fallback bypasses the HTTP bridge trim path and sends full input to Platform, producing high uncached input cost.

## What Changes

- Preserve backend Codex Platform fallback for stateless or unowned session-header requests.
- Suppress usage-drain Platform fallback for backend Codex HTTP requests that carry a ChatGPT `codex_session` affinity key while the pinned ChatGPT target remains selectable.
- Preserve the selectable ChatGPT owner for backend Codex `x-codex-turn-state` streaming requests instead of reallocating it solely because it is above the sticky budget threshold.
- Continue allowing Platform fallback when the pinned ChatGPT target is unavailable, outside rate-limit grace, paused, deactivated, or when `platform_fallback_force_enabled` is set.
- Add regression coverage for backend Codex session-header routing so continuity-preserving traffic stays on ChatGPT Web until the sticky owner is no longer selectable.

## Capabilities

### New Capabilities

### Modified Capabilities

- `responses-api-compat`: backend Codex HTTP Platform fallback must respect existing ChatGPT `codex_session` ownership before using usage-drain fallback.

## Impact

- Affected code: provider selection in `app/modules/proxy/service.py`, sticky target evaluation in `app/modules/proxy/load_balancer.py`, request capability derivation in `app/modules/proxy/api.py`.
- Affected tests: provider-selection unit tests and backend Codex Platform fallback integration tests.
- No database schema or external dependency changes.
