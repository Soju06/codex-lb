## Why

Operators who share pooled ChatGPT accounts with people outside the proxy (family, teammates using the ChatGPT web UI on the same logins) need a way to stop codex-lb from consuming an account's entire weekly (secondary-window) quota. Today the only hard stop is the upstream 100% exhaustion signal; every existing threshold (`drain_secondary_threshold_pct`, `sticky_reallocation_budget_threshold_pct`) is global and soft — a busy client can still ride one account to zero and lock out the human co-users.

## What Changes

- Add a nullable `weekly_usage_cap_pct REAL` column to `accounts` via a new alembic migration. `NULL` (the default) means "no cap" and preserves current routing exactly.
- Enforce the cap at a single choke point: `_build_states` in `app/modules/proxy/load_balancer.py` skips any account whose resolved secondary (weekly) window usage has reached its cap. Because every transport (responses, chat, websocket, compact, images) funnels account selection through this builder, one filter covers all routing, and sticky sessions rebind automatically through the existing "pinned account missing from candidates" path.
- The cap is evaluated against the resolved long-window usage (`AccountState.secondary_used_percent`), so weekly-only plans whose weekly usage arrives on the primary row are covered by the existing `_select_long_window_entry` resolution. Accounts with no usage data yet remain eligible (fail-open).
- When every account is capped, selection returns the existing "No available accounts" error; recovery is automatic as soon as a weekly window resets or the operator raises a cap.
- Add `PUT /api/accounts/{account_id}/weekly-usage-cap` (dashboard-session guarded) to set (`0–100`) or clear (`null`) the cap, mirroring the alias endpoint's layering; surface `weekly_usage_cap_pct` on `AccountSummary`.
- Add a small editor on the dashboard account detail usage panel so operators can set/clear the cap without API calls.

## Impact

- Fully backwards compatible: default `NULL` cap changes no behavior; migration is additive and idempotent on both SQLite and PostgreSQL.
- Enforcement granularity follows the background usage refresh interval (default 60s), so a capped account can slightly overshoot inside one poll window; this is acceptable for the quota-sharing use case.
- Requests pinned by `previous_response_id` / file-upload pins scope selection to a single account for continuity; when that account is capped they surface the existing no-account error rather than silently breaking continuity.
