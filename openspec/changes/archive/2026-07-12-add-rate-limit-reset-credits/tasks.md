## 1. Backend foundation (settings, upstream client, in-memory store)

- [x] 1.1 Add setting `rate_limit_reset_credits_refresh_interval_seconds` (default `60`) to `app/core/config/settings.py`; reset-credit polling itself is always on
- [x] 1.2 Create `app/core/clients/rate_limit_reset_credits.py` mirroring `app/core/clients/usage.py`: `fetch_reset_credits(access_token, account_id, *, base_url, timeout)` → GET `/wham/rate-limit-reset-credits`, and `consume_reset_credit(access_token, account_id, credit_id, *, base_url, timeout)` → POST `/wham/rate-limit-reset-credits/consume` with body `{credit_id, redeem_request_id: uuid4()}`. Reuse the same header-construction rules (skip `chatgpt-account-id` for `email_`/`local_` prefixes) and base-url normalization
- [x] 1.3 Define pydantic models for the upstream payloads: `ResetCreditItem` (id, reset_type, status, granted_at, expires_at, title, description, redeem_started_at, redeemed_at), `ResetCreditsResponse` (credits: list, available_count: int), `ConsumeResetCreditResponse` (code, credit, windows_reset)
- [x] 1.4 Create `app/modules/rate_limit_reset_credits/store.py` mirroring `app/modules/proxy/rate_limit_cache.py`: `RateLimitResetCreditsStore` with `anyio.Lock`-guarded `set(account_id, snapshot)`, `get(account_id) -> Snapshot | None`, `invalidate(account_id=None)`. Snapshot exposes `available_count`, `nearest_expires_at`, and the items list. Expose a module-level singleton + `get_rate_limit_reset_credits_store()` accessor

## 2. Backend scheduler, API, mapper, lifespan wiring

- [x] 2.1 Create `app/core/usage/reset_credits_refresh_scheduler.py` mirroring `app/core/usage/refresh_scheduler.py`: `RateLimitResetCreditsRefreshScheduler` dataclass with `asyncio.Lock`-guarded `_refresh_once` that runs in every replica, lists accounts, skips paused/deactivated/missing-`chatgpt-account-id`, decrypts `access_token_encrypted`, calls `fetch_reset_credits`, and stores the snapshot. On upstream error: log + retain prior snapshot; do NOT mutate account status. Add `build_rate_limit_reset_credits_scheduler()` factory
- [x] 2.2 Wire the new scheduler into `app/main.py` lifespan alongside `usage_scheduler`: build (~line 148), start (~154), stop (~314)
- [x] 2.3 Create `app/modules/rate_limit_reset_credits/api.py` with `GET /api/accounts/{account_id}/rate-limit-reset-credits` (returns cached snapshot or `null`) and `POST /api/accounts/{account_id}/rate-limit-reset-credits/consume` (selects soonest-`expires_at` available credit from the freshest snapshot, generates `redeem_request_id`, calls upstream, invalidates the cached snapshot, returns `{code, windows_reset, redeemed_at}`). Use `validate_dashboard_session` for GET and `require_dashboard_write_access` for POST. Return `409` when no credit is available. Register the router in `app/main.py`
- [x] 2.4 Extend the AccountSummary mapper(s) in `app/modules/accounts/` and the dashboard mapper to join the cached snapshot onto each returned account: add `available_reset_credits: int` (0 when no snapshot) and `reset_credit_nearest_expires_at: datetime | None` (null when no snapshot)
- [x] 2.5 Update the backend pydantic response schemas (`AccountSummary` / equivalent) to declare the two new fields

## 3. Frontend schemas, API client, formatter

- [x] 3.1 Add `availableResetCredits: number` and `resetCreditNearestExpiresAt: string | null` to `AccountSummary` in both `frontend/src/features/accounts/schemas.ts` and `frontend/src/features/dashboard/schemas.ts`
- [x] 3.2 Add `consumeRateLimitResetCredit(accountId): Promise<{ code: string; windowsReset: number; redeemedAt: string }>` to `frontend/src/features/accounts/api.ts` posting to `/api/accounts/{id}/rate-limit-reset-credits/consume`. On success, invalidate the `['accounts']` and `['dashboard']` TanStack Query keys
- [x] 3.3 Add `formatSingleUnitRemaining(expiresAtIso: string): { label: string; expiringSoon: boolean }` to `frontend/src/utils/formatters.ts`: `"${d}d"` for ≥1 day, `"${h}h"` for ≥1 hour, `"${m}m"` for ≥1 minute, `"now"` otherwise; `expiringSoon = ms < 7 * 86_400_000`. Sit it next to the existing `formatResetRelative`/`formatQuotaResetLabel` helpers

## 4. Frontend UI components

- [x] 4.1 Add the count badge to `frontend/src/features/accounts/components/account-list-item.tsx`: an absolutely-positioned circle on the right-upper radius showing the integer count or `"99+"` when `> 99`. Render only when `availableResetCredits > 0`
- [x] 4.2 Add the `Reset (N)` button to `frontend/src/features/accounts/components/account-actions.tsx` immediately after the Export button, matching its `size="sm" variant="outline" className="h-8 gap-1.5 text-xs"` style, with a `RotateCcw` icon, a single-unit countdown label (using 3.3) placed at the button's right-upper radius, and destructive/red label color when `expiringSoon`. Render only when `availableResetCredits > 0`. Wire `onClick` to open the confirmation dialog
- [x] 4.3 Add a reset action to `frontend/src/features/dashboard/components/account-list.tsx` (table view) inside the existing Details action cell, matching the `h-7 w-7` icon-button style with the countdown and count exposed in the `title` tooltip. Render only when `availableResetCredits > 0`
- [x] 4.4 Add a `Reset (N)` button to `frontend/src/features/dashboard/components/account-card.tsx` (grid view) next to the Details button, matching the `h-7 gap-1.5` text style with the single-unit countdown label. Render only when `availableResetCredits > 0`
- [x] 4.5 Implement the confirmation dialog (reuse `frontend/src/components/confirm-dialog.tsx` + `frontend/src/hooks/use-dialog-state.ts`, same shape as the delete-account dialog): body describes the soonest-expiring banked reset-credit redeem action and shows `expires_at` formatted as local `YYYY-MM-DD HH:MM:SS` when credit details are available. On confirm → call `consumeRateLimitResetCredit(accountId)` → success/failure toast → query invalidation
- [x] 4.6 Add a "Most reset credits" option to the Accounts page sort selector in `frontend/src/features/accounts/sorting.ts` and make it the default Accounts page sort mode: comparator orders by `availableResetCredits` desc, tiebreak by `resetCreditNearestExpiresAt` asc (soonest first), accounts with null expiry last. Add the localized dropdown label
- [x] 4.7 Add a summed reset-credit badge to `frontend/src/components/layout/app-header.tsx` for the Accounts nav tab, capped at `99+`

## 5. Tests

- [x] 5.1 Backend — `app/core/clients/rate_limit_reset_credits.py`: header construction (account-id skip rule), base-url normalization, consume body shape, JSON parse on 200, error handling on non-200/non-JSON
- [x] 5.2 Backend — `app/modules/rate_limit_reset_credits/store.py`: `set`/`get`/`invalidate` (single + all), concurrency under `anyio.Lock`, missing-account returns `None`
- [x] 5.3 Backend — `reset_credits_refresh_scheduler.py`: every replica refreshes its local cache, paused/deactivated account skip, one-account failure doesn't break the loop, upstream error retains prior snapshot, account status is never mutated
- [x] 5.4 Backend — `rate_limit_reset_credits/api.py`: GET returns cached snapshot / `null` on miss; POST selects soonest expiry, calls upstream with fresh `redeem_request_id`, invalidates cache, returns `{code, windows_reset, redeemed_at}`; write-access gating refuses guests; `409` when no available credit
- [x] 5.5 Backend — AccountSummary mapper: exposes the two new fields from a cached snapshot, returns `0`/`null` when no snapshot, does not crash when store is empty
- [x] 5.6 Frontend — `formatSingleUnitRemaining`: boundaries at 7d (color flip), 1d, 1h, 1m, and `now`; sub-minute and past timestamps both yield `"now"`
- [x] 5.7 Frontend — `AccountListItem` badge: renders count, `"99+"` at 100+, absent at 0
- [x] 5.8 Frontend — Reset button visibility: rendered when `availableResetCredits > 0`, absent at 0, in all three surfaces (account-actions, dashboard table, dashboard grid)
- [x] 5.9 Frontend — confirm dialog → consume: confirmation calls `consumeRateLimitResetCredit`, shows the expiry in local `YYYY-MM-DD HH:MM:SS`, success path invalidates queries, failure path surfaces a toast and does not invalidate
- [x] 5.10 Frontend — "Most reset credits" sort: comparator orders by count desc with soonest-expiry tiebreak, null-expiry accounts last
- [x] 5.11 Frontend — Accounts nav badge: shows the summed total, caps at `99+`, and hides at zero

## 6. Validation and OpenSpec hygiene

- [x] 6.1 Run `openspec validate add-rate-limit-reset-credits --strict` and resolve any findings
- [x] 6.2 Run `openspec validate --specs --strict` to confirm no main-spec drift
- [x] 6.3 Run backend checks: `uv run ruff check && uv run ruff format --check && uv run pytest` (or the repo's documented equivalent)
- [x] 6.4 Run frontend checks: `pnpm -C frontend lint && pnpm -C frontend typecheck && pnpm -C frontend test` (or the repo's documented equivalent)
- [x] 6.5 Manually verify the three Reset button placements, the per-button count labels, the Accounts-nav total badge cap behavior, the countdown color flip at 7d, the local expiry timestamp, the confirm flow, and the new sort option against the spec scenarios (verified 2026-07-13 against the shipped frontend via code + test evidence; see "Follow-up verification (2026-07-13)" in verify-report.md)
