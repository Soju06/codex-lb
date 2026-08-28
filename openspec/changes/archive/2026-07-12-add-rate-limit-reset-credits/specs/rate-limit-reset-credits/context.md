# Rate-Limit Reset Credits Context

## Purpose

codex-lb polls OpenAI's banked ("savable") rate-limit reset credits per account, caches them
in memory, and lets dashboard operators redeem the soonest-expiring credit for any account
without leaving the dashboard. The credit is a ChatGPT-subscription entitlement granted by
OpenAI; codex-lb is spending a credit OpenAI already gave the account — it does not bypass
any rate limit.

## Upstream Source

The credits endpoints live under `https://chatgpt.com/backend-api/wham`:

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/wham/rate-limit-reset-credits` | GET | List banked credits + `available_count` |
| `/wham/rate-limit-reset-credits/consume` | POST | Redeem one credit (body: `credit_id`, `redeem_request_id`) |

Both require `Authorization: Bearer <access_token>` and `chatgpt-account-id: <account_id>`
headers. The consume body returns `{code, credit: {id, status, redeemed_at, ...}, windows_reset}`.

These endpoints are undocumented and were reverse-engineered from the official
`openai.chatgpt` VS Code extension's webview bundle. The canonical external reference is
[`aaamosh/codex-reset`](https://github.com/aaamosh/codex-reset) — a single-account CLI
implementation that codex-lb's multi-account, dashboard-driven, in-memory-cached variant
is based on. OpenAI may rename, gate, or remove these endpoints at any time; the codex-lb
client treats non-200, non-JSON, and schema-drifted 200 responses defensively.

## Decisions

- **In-memory only.** No DB column, no migration. Each replica refreshes its own process-local
  snapshots, which repopulate within one tick of startup. Restart cost: up to 60s of
  `available_reset_credits: 0` on that replica.
- **Server picks the credit, not the client.** `POST /consume` takes only the account id;
  the server selects the soonest-expiring available credit from the freshest snapshot and
  generates the `redeem_request_id`. Avoids stale-UI and clock-skew races.
- **Never mutates account status.** Account status is owned by usage refresh
  (see `usage-refresh-policy`). Reset-credit polling failure logs and retains the prior
  snapshot; it does not deactivate, rate-limit, or quota-block any account.
- **Dedicated scheduler, not folded into usage refresh.** Reuses the `UsageRefreshScheduler`
  loop shape (`asyncio.Lock`-guarded, configurable cadence) but intentionally does not use
  leader election because the cache is process-local. The scheduler always starts with the
  app; only the interval is configurable. See `design.md` for the rationale.

## Failure Modes

- **Upstream returns 200 but the rate-limit window doesn't move.** Per upstream behavior
  the credit is still consumed. The dashboard requires explicit confirmation before
  redeeming; on success we invalidate the cache and let the next tick reconcile
  `available_count`.
- **Snapshot is empty/stale.** UI hides all reset affordances for that account
  (`available_reset_credits: 0`). Not an error — wait one tick.
- **Fresh consume preflight disproves a cached credit.** If the live pre-consume fetch says
  `available_count: 0` or returns no available items, codex-lb overwrites the cached snapshot
  with that fresh upstream state before returning `409`, so the dashboard does not keep
  advertising a stale `Reset (N)` action until the next scheduler tick.
- **Account becomes ineligible after a successful snapshot.** Scheduler skips paused,
  reauth-required, deactivated, or account-id-less accounts, so dashboard reads also check
  current account eligibility before serving cached reset credits. If the account is
  ineligible, the read returns no snapshot and invalidates the stale cache entry.
- **Upstream 401/403/auth-expired.** Logged; prior snapshot retained. Does NOT deactivate
  the account. If the token is genuinely expired, usage refresh / OAuth refresh owns the
  deactivation path.
- **Concurrent consume clicks.** Redemption is serialized per account so two overlapping
  consume requests cannot forward the same cached `credit_id` upstream. After the first
  request finishes, the second request re-reads the account snapshot and either sees a
  refreshed state or fails with a dashboard conflict when no credit is still available.
- **Successful self-service redeem leaves stale usage state behind.** The `/v1/reset-credit`
  success path force-refreshes usage for the redeemed account and invalidates the
  load-balancer's account-selection cache when that refresh writes updated usage, so
  `rate_limited` / `quota_exceeded` recovery can take effect for immediate follow-up
  `/v1/*` traffic instead of waiting for the next periodic usage tick.
- **Upstream consume failures.** Client-facing upstream failures are preserved as dashboard
  errors (`401`, `403`, `409`), while other consume failures surface as dashboard `503`
  responses instead of falling into the generic internal-error handler.

## Example: list response

```json
{
  "credits": [
    {
      "id": "RateLimitResetCredit_test",
      "reset_type": "codex_rate_limits",
      "status": "available",
      "granted_at": "2026-06-12T01:29:41.346025Z",
      "expires_at": "2026-07-12T01:29:41.346025Z",
      "redeem_started_at": null,
      "redeemed_at": null,
      "profile_image_url": "https://openaiassets.blob.core.windows.net/$web/codex/codex-icon-200.png",
      "profile_user_id": "Codex Team",
      "title": "One free rate limit reset",
      "description": "Thanks for using Codex! You've been granted one free rate limit reset."
    }
  ],
  "available_count": 1
}
```

## Example: consume response

```json
{
  "code": "reset",
  "credit": {
    "id": "RateLimitResetCredit_...",
    "reset_type": "codex_rate_limits",
    "status": "redeemed",
    "redeemed_at": "2026-06-13T13:12:31Z"
  },
  "windows_reset": 1
}
```

## Operational Notes

- The 60s cadence matches usage refresh, but each replica polls because each replica serves
  dashboard reads from its own process-local snapshot cache.
- A credit is consumed as soon as upstream returns 200 — treat the confirmation dialog as
  the point of no return.

## Related Work

- Reference CLI: [`aaamosh/codex-reset`](https://github.com/aaamosh/codex-reset)
- Sibling capability: [`usage-refresh-policy`](../../specs/usage-refresh-policy/) — owns
  account-status derivation and the `/wham/usage` 60s polling pattern this mirrors
- OpenAI announcement: [Flexible rate-limit resets for Codex](https://community.openai.com/t/flexible-rate-limit-resets-for-codex/1383470)
