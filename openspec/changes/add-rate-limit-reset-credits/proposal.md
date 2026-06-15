## Why

Operators need to see banked rate-limit reset credits per account without manually inspecting upstream state, and redeem them when an account is rate-limited.

## What Changes

- fetch `GET /wham/rate-limit-reset-credits` every 60 seconds for refreshable accounts
- persist new credits keyed by `(account_id, credit_id)`
- locally mark stored rows `expired` when `now > expires_at`
- expose `availableResetCount` through shared account payloads
- show reset counts in dashboard/accounts UI
- enable operator-triggered redemption via `POST /wham/rate-limit-reset-credits/consume`
- select the nearest-expiry available credit for redemption
- show a confirmation dialog before submitting the consume request
- mark redeemed credits in the local database after a successful consume response
