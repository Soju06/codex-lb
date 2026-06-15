## Upstream endpoints

### Fetch

- path: `GET /wham/rate-limit-reset-credits`
- auth: `Authorization: Bearer <token>`
- account header: `chatgpt-account-id`

This fetch runs inside the existing 60-second background refresh loop and is scoped to the same account that is already being refreshed for usage data.

### Consume

- path: `POST /wham/rate-limit-reset-credits/consume`
- auth: `Authorization: Bearer <token>`
- account header: `chatgpt-account-id`
- body: `{"credit_id": "...", "redeem_request_id": "<uuid4>"}`

The operator-triggered API surface is `POST /api/accounts/{account_id}/reset-credit`, which internally selects the nearest-expiry available credit, generates a UUID v4 `redeem_request_id`, calls the upstream consume endpoint, and marks the credit as `redeemed` on success.

## Persistence rule

- insert new `(account_id, credit_id)` rows only
- never overwrite existing upstream-fetched fields
- allow local `available -> expired` transition when `now > expires_at`
- allow local `available -> redeemed` transition after a successful consume

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
- If a stored credit expires locally before upstream stops returning it, this change treats the local `expired` state as authoritative for the available count.
- If the consume endpoint returns an error, the credit remains `available` and the UI shows the error.

## UI notes

- The Accounts navigation badge uses an inline circular badge style on the nav tab.
- On the Accounts page, each list-item box shows a circular corner badge pinned to the top-right of the box when `availableResetCount > 0`. The badge is hidden when the count is zero.
- The dashboard account table and account card do NOT show a badge next to the status. Instead, both surfaces show a ghost `Reset (N)` button next to the Details action when `availableResetCount > 0`, and hide it when the count is zero.
- On the account detail panel, the ghost `Reset (N)` button is positioned next to `Export`.
- Clicking any Reset button opens a confirmation dialog before executing the consume request.
- When `nearestResetExpiryAt` is within 7 days, the Reset button applies a red border and red text styling to signal urgency.
- The urgency check uses a standalone utility function (not inline `Date.now()` in render) to comply with React purity rules.
