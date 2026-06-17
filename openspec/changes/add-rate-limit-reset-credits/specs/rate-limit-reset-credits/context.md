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
client treats non-200/non-JSON responses defensively.

## Decisions

- **In-memory only.** No DB column, no migration. Snapshots repopulate within one tick of
  startup. Restart cost: up to 60s of `available_reset_credits: 0` everywhere.
- **Server picks the credit, not the client.** `POST /consume` takes only the account id;
  the server selects the soonest-expiring available credit from the freshest snapshot and
  generates the `redeem_request_id`. Avoids stale-UI and clock-skew races.
- **Never mutates account status.** Account status is owned by usage refresh
  (see `usage-refresh-policy`). Reset-credit polling failure logs and retains the prior
  snapshot; it does not deactivate, rate-limit, or quota-block any account.
- **Dedicated scheduler, not folded into usage refresh.** Reuses the exact
  `UsageRefreshScheduler` shape (leader-gated, `asyncio.Lock`-guarded, configurable
  cadence) but keeps the two upstream calls decoupled. See `design.md` for the rationale.

## Failure Modes

- **Upstream returns 200 but the rate-limit window doesn't move.** Per upstream behavior
  the credit is still consumed. The confirmation dialog warns the operator; on success we
  invalidate the cache and let the next tick reconcile `available_count`.
- **Snapshot is empty/stale.** UI hides all reset affordances for that account
  (`available_reset_credits: 0`). Not an error — wait one tick.
- **Upstream 401/403/auth-expired.** Logged; prior snapshot retained. Does NOT deactivate
  the account. If the token is genuinely expired, usage refresh / OAuth refresh owns the
  deactivation path.
- **Concurrent consume clicks.** Server re-selects from the snapshot each call; the second
  click either consumes a different credit (if multiple were available) or surfaces
  upstream's "no available credit" error.

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

- Toggle polling without a deploy by setting `rate_limit_reset_credits_refresh_enabled=false`
  and restarting. The store empties and all UI reset affordances disappear.
- The 60s cadence matches usage refresh; both are leader-gated, so adding accounts scales
  upstream load by the same factor usage refresh already does.
- A credit is consumed as soon as upstream returns 200 — treat the confirmation dialog as
  the point of no return.

## Related Work

- Reference CLI: [`aaamosh/codex-reset`](https://github.com/aaamosh/codex-reset)
- Sibling capability: [`usage-refresh-policy`](../../specs/usage-refresh-policy/) — owns
  account-status derivation and the `/wham/usage` 60s polling pattern this mirrors
- OpenAI announcement: [Flexible rate-limit resets for Codex](https://community.openai.com/t/flexible-rate-limit-resets-for-codex/1383470)
