# Recorded subscription term

The [subscription-term requirements](spec.md) let operators compare remaining paid periods on the Accounts List, Grid and selected detail. These dates are informational and do not drive routing, renewal, or account health.

## Source and interpretation

The stored ID token can carry `chatgpt_subscription_active_until` and `chatgpt_subscription_last_checked` under `https://api.openai.com/auth`. The account summary mapper reuses its existing credential decryption to expose these two dates as `subscription.activeUntil` and `subscription.lastCheckedAt`. No new billing endpoint, upstream request, database column, or token re-import is involved. An aggregate metadata audit during development found explicit end dates in 129 of 131 stored Plus credentials; availability is account-dependent.

The deadline represents the period recorded in that credential. Automatic renewal can extend a live subscription after the token snapshot was issued. Consequently, elapsed dates are labeled “Recorded period elapsed”; they are not evidence that the account is disabled or that payment failed. Existing token refresh/import behavior supplies subsequent snapshots.

## Missing and historical data

Null, absent, malformed, and out-of-range values remain unknown independently of the rest of the token. A token's `exp`, quota reset, and local refresh timestamp are not substitutes. A current free/unknown plan, or a mismatch between the token's plan and current stored plan, suppresses old paid-term dates. Upstream ISO datetimes without an offset are interpreted as UTC, matching these token claims; dashboard responses emit explicit offsets.

## Example

Given a Plus credential with an active-until timestamp of `2026-10-10T00:00:00Z`, the Accounts page shows the remaining duration, October 10 as the recorded end date, and the last-checked date if present. If the access token expires September 28, that expiry remains separately visible under Token Status. If no subscription deadline exists, the subscription panel displays “No data”.

## Account overview

Accounts defaults to its original Detail mode, with account selection on the left and statistics/charts inline on the right. The additional List and Grid overviews are remembered under `codex-lb-accounts-view-mode`, independently of the Dashboard view. All three modes retain search, status filters and sorting. List and Grid render at most 24 accounts per page using existing summary data; selecting an overview account opens management in a dialog. Only the inline selected account or open dialog requests trend/reset-credit data. One page-level timer updates subscription countdowns each minute.

The frontend accepts both historical naive UTC `lastRefreshAt` and current offset-bearing values so a rolling deployment can serve either backend version. Older summaries without a `subscription` field remain valid and display an unknown term.

## Compact presentation

The original Detail selector shows remaining time beneath the status badge, and the minimal List has a dedicated duration column. Both use zero-padded whole days and hours with literal d/h suffixes: `18d 08h`, or `00d 00h` during a positive period shorter than an hour. The elapsed label is distinct, so rounding cannot imply expiration. The shared page clock keeps all rows aligned. Recorded end date, last check and renewal limitations remain in the tooltip/accessibility description; Grid and selected details keep the full visible presentation.

For example, at September 27, 2026 12:00 UTC, a deadline of October 15, 2026 20:00 UTC reads `18d 08h` under Active. A missing deadline reads “No data” even if an access-token expiry exists; a passed deadline reads “Elapsed” without changing Active or routing eligibility.
