## Context

codex-lb already polls upstream `GET /wham/usage` per account on a 60s leader-gated loop (`app/core/usage/refresh_scheduler.py`) and uses an in-memory cache pattern (`app/modules/proxy/rate_limit_cache.py`) for short-lived values. Per-account OAuth bearer tokens are stored encrypted at rest (`Account.access_token_encrypted`) and decrypted on demand via `TokenEncryptor`. Dashboard endpoints authenticate via `validate_dashboard_session` + `require_dashboard_write_access`.

OpenAI added banked rate-limit reset credits on 2026-06-12. The upstream API surface (reverse-engineered and documented in the [`aaamosh/codex-reset`](https://github.com/aaamosh/codex-reset) reference) is:

- `GET /wham/rate-limit-reset-credits` → `{credits: [...], available_count: N}` with `Authorization: Bearer <token>` + `chatgpt-account-id: <account_id>` headers
- `POST /wham/rate-limit-reset-credits/consume` with body `{credit_id, redeem_request_id}` → `{code, credit, windows_reset}`

The reference is a single-account CLI; codex-lb needs a multi-account, dashboard-driven, in-memory-cached variant.

## Goals / Non-Goals

**Goals:**
- Per-account background poll of reset credits every 60s, cached in-memory keyed by account id
- Dashboard operators can see, per account: how many banked credits are available and when the soonest one expires
- Dashboard operators can redeem the soonest-expiring credit for any account from three surfaces (Accounts action bar, Dashboard table, Dashboard grid) with a confirmation dialog
- Dashboard operators can see the summed available reset-credit count on the top-nav Accounts tab
- Sort the Accounts page by available reset credits
- Reuse existing token decryption, scheduler shape, in-memory cache shape, dashboard auth, confirmation dialog, and formatter conventions — no new frameworks

**Non-Goals:**
- No DB persistence — snapshots live only in memory and are repopulated after restart
- No referral/invite logic from the reference repo
- No changes to `/wham/usage` or account status derivation (rate_limited / quota_exceeded reconciliation stays owned by usage refresh)
- No live-ticking countdown — values recompute on each 60s scheduler tick + TanStack Query refetch, matching the existing `formatQuotaResetLabel` pattern
- No new top-nav account card; the badge lives on `AccountListItem` only
- No mobile-specific behavior

## Decisions

### Decision: Dedicated module + scheduler, mirroring `usage_refresh` (not folding into the usage loop)
**Rationale:** Usage refresh owns account-status derivation and has a dense, scenario-heavy spec (`usage-refresh-policy`) tying it to quota reconciliation, cooldowns, and warm-up. Bolting credits onto that loop would couple two upstream calls and their failure modes, and muddy the usage-refresh contract. A dedicated `RateLimitResetCreditsRefreshScheduler` reuses the `UsageRefreshScheduler` loop shape (`asyncio.Lock`-guarded `_refresh_once`, interval-only configuration) and always starts with the application. Unlike usage refresh, it deliberately runs on every replica because reset-credit snapshots are process-local and dashboard reads must be consistent regardless of which replica handles the request.
**Alternatives considered:** (a) Fold into `UsageRefreshScheduler._refresh_once` — rejected for the coupling above. (b) Pure passthrough via the local `wham_router` proxy — rejected because the dashboard needs the in-memory store and per-account token decryption that the proxy router does not have, and the requirement is "refresh every 60s + store in-memory."

### Decision: Server picks the soonest-expiring credit at consume time
**Rationale:** Single source of truth. The client passes only `{account_id}` to `POST /consume`; the server reads the cached snapshot, selects the available credit with the smallest `expires_at`, generates `redeem_request_id = uuid4()`, and calls upstream. This guarantees "nearest expiry_at is selected" even if the UI is stale, and avoids a client/server clock skew race.
**Alternatives considered:** Client sends the specific `credit_id` — rejected because the cached snapshot may have changed between render and click (e.g. one expired or was redeemed elsewhere).

### Decision: Expose `available_reset_credits` + `reset_credit_nearest_expires_at` on `AccountSummary` (no DB column)
**Rationale:** The Accounts-page and Dashboard list both consume `AccountSummary`; joining the cached snapshot at mapper time gets the data to every UI surface with one change and zero migration. Account rows that have no cache yet return `0` / `null` and the UI hides its reset affordances for them.
**Alternatives considered:** Separate `/api/accounts/{id}/rate-limit-reset-credits` GET consumed per-card — rejected because it adds N round-trips and N re-renders; the count belongs on the summary the UI already fetches.

### Decision: Countdown is single-unit and goes red under 7 days
**Rationale:** User requirement: one unit only ("6d" / "13h" / "45m" / "now"), red when `< 7d`. A new `formatSingleUnitRemaining(expiresAtIso)` helper sits next to existing `formatQuotaResetLabel` / `formatResetRelative` in `utils/formatters.ts`; the caller colors it via `ms < 7 * DAY_MS`. We do NOT add a ticking hook (matches the existing reset-label pattern). The confirmation dialog uses a separate local-time formatter with exact `YYYY-MM-DD HH:MM:SS` output so operators can see the precise expiry instant in their own timezone.
**Alternatives considered:** Reuse `formatResetRelative` — rejected because it returns multi-unit ("6d 13h") output.

### Decision: Reset credit refresh never mutates account status
**Rationale:** Account status (active / rate_limited / quota_exceeded / paused / deactivated) is owned by usage refresh. Reset-credit polling failure MUST NOT deactivate or block an account — doing so would create a second status owner and contradict `usage-refresh-policy`. On upstream errors the scheduler logs, keeps the prior snapshot if any, and moves on.
**Alternatives considered:** Reuse usage-refresh cooldown/deactivation classification — rejected because it would require this scheduler to write account status, violating the single-owner invariant.

## Risks / Trade-offs

- **[Upstream endpoints are undocumented]** → Mitigation: client treats non-200 / non-JSON defensively, logs, keeps prior snapshot; consume-failure surfaces to UI as a toast without invalidating the cache. Document the upstream-dependence caveat in the capability `context.md`.
- **[In-memory cache lost on restart]** → Mitigation: acceptable per requirements; the next 60s tick repopulates. UI treats missing snapshot as `available_reset_credits: 0` (hidden affordances), not an error.
- **[Credit consumed even on partial reset]** (upstream behavior: "if POST returns 200, the credit is gone") → Mitigation: require an explicit confirmation step before redeeming. On success we invalidate the cache and let the next tick reconcile.
- **[Race: credit expires between render and click]** → Mitigation: server re-selects from the freshest cached snapshot at consume time and surfaces upstream's error if the chosen credit is no longer redeemable.
- **[Many accounts = many upstream calls per tick]** → Mitigation: reuse the same skip rules (paused/deactivated/missing chatgpt-account-id) and keep the interval configurable. Each replica polls so its process-local cache is useful for dashboard reads; moving snapshots to shared storage can later reduce duplicate polling if upstream load becomes a problem.
- **[Guest read-only dashboard users]** → Mitigation: `POST /consume` requires `require_dashboard_write_access`; guests can see the badge/button (count is read off `AccountSummary`) but cannot redeem.

## Migration Plan

- No DB migration (in-memory only). No env var required beyond the refresh interval setting.
- Deploy is a single rolling restart; the first tick after boot repopulates snapshots within 60s.
- Rollback: revert the deploy; there is no separate disable toggle for reset-credit polling.

## Open Questions

None blocking. (The two upstream endpoints' longevity is a runtime risk documented in `context.md`, not a design unknown.)
