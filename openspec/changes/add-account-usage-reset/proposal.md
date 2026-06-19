## Why

OpenAI rolled out banked Codex rate-limit reset credits in June 2026. Operators can spend them from Codex Desktop or the VS Code extension, but codex-lb only mirrors `/wham/usage` windows and exposes force-probe (#677). There is no way to see whether an account has a saved reset available or to apply one from the dashboard/API ([#1014](https://github.com/Soju06/codex-lb/issues/1014)).

## What Changes

- Parse and persist `rate_limit_reset_credits.available_count` from existing `/wham/usage` refreshes on primary `usage_history` rows.
- Expose `rate_limit_reset_available_count` on `AccountSummary` / dashboard account list.
- Add `POST /api/accounts/{account_id}/usage-reset/apply` (dashboard write auth) that consumes one upstream credit via `POST /wham/rate-limit-reset-credits/consume`, then `force_refresh`s usage.
- Dashboard: availability badge + **Apply reset** action with confirmation (distinct from **Force probe**).
- Audit log entry `account_usage_reset_applied`.

## Impact

- Operators can manually restore exhausted accounts when banked credits exist, without leaving codex-lb.
- Manual-only; never auto-applies. Consuming a credit is irreversible.
- Aligns with official OpenAI Codex backend client (`openai/codex#28143`).
- Force probe (#676 lazy limiter) and apply reset (banked credit) remain separate operator tools.