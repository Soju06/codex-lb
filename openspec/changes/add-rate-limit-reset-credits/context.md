## Upstream endpoint

- path: `/wham/rate-limit-reset-credits`
- auth: `Authorization: Bearer <token>`
- account header: `chatgpt-account-id`

This fetch runs inside the existing 60-second background refresh loop and is scoped to the same account that is already being refreshed for usage data.

## Persistence rule

- insert new `(account_id, credit_id)` rows only
- never overwrite existing upstream-fetched fields
- allow local `available -> expired` transition when `now > expires_at`

## Example

If account `acc_123` refreshes at `12:00:00Z` and upstream returns:

```json
{
  "credits": [
    {
      "id": "RateLimitResetCredit_one",
      "status": "available",
      "granted_at": "2026-06-12T01:29:41Z",
      "expires_at": "2026-07-12T01:29:41Z",
      "redeemed_at": null
    }
  ],
  "available_count": 1
}
```

codex-lb stores one new row for `(acc_123, RateLimitResetCredit_one)`. A later refresh that returns the same credit does not insert a duplicate or overwrite the stored upstream fields. If a later local refresh pass observes `now > expires_at`, codex-lb changes only the stored status to `expired` so `availableResetCount` drops to zero.

## Failure and edge cases

- A fetch failure for one account does not clear previously stored credits and does not block refresh work for other accounts.
- The available count is derived from persisted rows, so one transient upstream failure does not zero the UI count for that account.
- If upstream keeps returning a credit that is already stored, the row remains deduplicated by `(account_id, credit_id)`.
- If a stored credit expires locally before upstream stops returning it, this change still treats the local `expired` state as authoritative for the available count until a future change defines overwrite or redemption behavior.

## UI notes

- The Accounts navigation badge and per-account badges are intended to use the same compact circular badge style already used elsewhere in the dashboard.
- The disabled `Reset (N)` control is present for visibility only in this read-only rollout.
- On the account detail view, the disabled `Reset (N)` control remains positioned next to `Export` so operators can see the intended future workflow entry point without consume behavior in this change.

## Future follow-up

Consume remains a separate change so this rollout is read-only.
