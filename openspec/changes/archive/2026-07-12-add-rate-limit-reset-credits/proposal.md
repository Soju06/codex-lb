## Why

OpenAI rolled out savable ("banked") rate-limit reset credits for Codex on 2026-06-12. Eligible ChatGPT plans receive credits that can be redeemed to reset rate-limit windows, but the redeem affordance only ships in the desktop app and VS Code/Cursor/Windsurf extension — not in the Codex CLI, and not in any operator surface. codex-lb operators managing many accounts have no way to see how many banked resets an account has, when they expire, or to redeem one without leaving the dashboard. We need first-class visibility and a one-click redeem action that reuses each account's existing OAuth bearer token.

## What Changes

- Add a per-account background poller that calls upstream `GET /wham/rate-limit-reset-credits` every 60s using each account's stored bearer token, and caches the result in-memory keyed by account id.
- Add a dashboard endpoint `POST /api/accounts/{account_id}/rate-limit-reset-credits/consume` that redeems the soonest-expiring available credit by calling upstream `POST /wham/rate-limit-reset-credits/consume` with `{credit_id, redeem_request_id}`.
- Expose `available_reset_credits` count and `reset_credit_nearest_expires_at` timestamp on the account summary payloads consumed by the Accounts page and Dashboard.
- Accounts page: add a `Reset (N)` button to the per-account action bar (next to Export), a count badge on each `AccountListItem` (capped at "99+"), and a new "Most reset credits" option in the sort-mode dropdown.
- Dashboard Accounts section: add a reset action next to Details in both the table and grid views, with the grid label rendered as `Reset (N)`.
- Show a single-unit countdown ("6d" / "13h" / "45m" / "now") of the nearest credit's expiry on each Reset button; render it in destructive/red when less than 7 days remain.
- Show the confirmation-dialog expiry in local time using `YYYY-MM-DD HH:MM:SS`.
- Show the total available reset-credit count on the top-nav Accounts tab, pinned to the upper-right radius and capped at `99+`.
- Gate the Reset button and badge on `available_reset_credits > 0`; gate the redeem endpoint on dashboard write access (read-only guests cannot redeem).

## Capabilities

### New Capabilities
- `rate-limit-reset-credits`: Background polling, in-memory caching, and dashboard-initiated redemption of upstream Codex banked rate-limit reset credits per account.

### Modified Capabilities
- `frontend-architecture`: New dashboard/Accounts UI elements — Reset button (Accounts tab + Dashboard), count badge on AccountListItem, top-nav Accounts total badge, expiry countdown label, local expiry timestamp formatting, and a new Accounts sort mode by available reset credits.

## Impact

- **Backend (new)**: `app/core/clients/rate_limit_reset_credits.py` (upstream client), `app/modules/rate_limit_reset_credits/store.py` (in-memory store + singleton), `app/core/usage/reset_credits_refresh_scheduler.py` (60s per-replica loop), `app/modules/rate_limit_reset_credits/api.py` (dashboard GET + consume POST).
- **Backend (modified)**: `app/main.py` lifespan wiring (build/start/stop the new scheduler); `app/core/config/settings.py` (refresh interval setting only); account-summary mappers in `app/modules/accounts/` and the dashboard mapper to join the cached snapshot onto `AccountSummary`.
- **Frontend (new/modified)**: `features/accounts/schemas.ts` + `features/dashboard/schemas.ts` (two new fields); `features/accounts/api.ts` (consume client); `features/accounts/sorting.ts` (new sort mode); `features/accounts/components/account-actions.tsx` (Reset button); `features/accounts/components/account-list-item.tsx` (count badge); `features/dashboard/components/account-list.tsx` + `account-card.tsx` (Reset button); `components/layout/app-header.tsx` (Accounts total badge); `utils/formatters.ts` (single-unit countdown + local expiry timestamp formatter); reuse of `components/confirm-dialog.tsx` + `hooks/use-dialog-state.ts` for the confirmation flow.
- **Upstream contract**: undocumented OpenAI endpoints under `https://chatgpt.com/backend-api/wham/rate-limit-reset-credits`; behavior is best-effort and may change upstream (see context doc).
- **In-memory only**: no DB schema migration; snapshots are lost on restart and repopulated on the next tick.
- **Tests**: pytest for client/store/scheduler/API/mapper; vitest (or equivalent) for formatter boundaries, badge cap, button visibility, confirm flow, and sort comparator.
