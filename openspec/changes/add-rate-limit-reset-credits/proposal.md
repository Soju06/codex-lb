## Why

Operators need to see banked rate-limit reset credits per account without manually inspecting upstream state.

## What Changes

- fetch `GET /wham/rate-limit-reset-credits` every 60 seconds for refreshable accounts
- persist new credits keyed by `(account_id, credit_id)`
- locally mark stored rows `expired` when `now > expires_at`
- expose `availableResetCount` through shared account payloads
- show reset counts in dashboard/accounts UI

## Out of Scope

- `POST /wham/rate-limit-reset-credits/consume`
- `credit_id` selection
- `redeem_request_id` generation
- any reset redemption workflow
