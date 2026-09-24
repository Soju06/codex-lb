## Why

Codex subagents currently inherit the parent process-session account even when other eligible accounts are available, so a long-lived parent and its parallel workers consume the same account budget. Operators need an explicit, continuity-safe way to spread fresh child work without weakening account-bound response continuation.

## What Changes

- Add an opt-in subagent account preference with `off`, `parent_bound_only`, and `always` modes.
- In `parent_bound_only`, prefer a different account only when durable routing evidence proves the parent has used `previous_response_id`; shared process/session metadata alone is insufficient.
- Apply the preference only to a new child's first account-neutral selection, fall back to the parent account when no eligible alternative exists, and retain the child's independently established affinity afterward.
- Preserve all response, conversation, turn-state, file, bridge, source-pin, security, model, API-key, quota, and health constraints.
- Expose the mode in dashboard routing settings, defaulting to `off` with no new environment variable.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `account-routing`: Define the optional preference and fallback behavior for account-neutral subagent admission.
- `responses-api-compat`: Define reliable parent/subagent metadata interpretation and positive previous-response binding evidence.
- `frontend-architecture`: Expose the persisted three-mode preference in routing settings.

## Impact

The proxy request context, continuity evidence, account selection, dashboard settings schema/storage/API, dashboard routing UI, database migration, diagnostics, and their tests are affected. Existing installations retain current behavior because the persisted setting defaults to `off`/inheritance-free disabled behavior.
