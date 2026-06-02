## Why

Accounts can exhaust their weekly ChatGPT usage percentage while still having usable credit-backed capacity. The proxy routing path must keep those accounts selectable when the upstream usage snapshot reports usable credits, and the accounts/dashboard summary path must show the same effective status. If those paths disagree, operators see an account as `quota_exceeded` even though proxy selection treats it as usable.

## What Changes

- Treat exhausted secondary-window usage as usable when the selected usage snapshot reports `credits_unlimited`, `credits_has`, or a positive `credits_balance`.
- Apply the same credit-aware quota interpretation in proxy account selection and account-summary status mapping.
- Preserve existing primary-window precedence: exhausted primary usage still produces `rate_limited`.
- Preserve operator-disabled states: paused or deactivated accounts are not reactivated by credit snapshots.

## Capabilities

### Modified Capabilities

- `usage-refresh-policy`: effective account status derived from usage snapshots must honor usable credit fields consistently across routing and dashboard/account summaries.

## Impact

- **Code**: existing shared quota helper plus proxy/account-summary call sites.
- **Tests**: unit coverage for credit-backed secondary exhaustion in proxy selection and account summaries.
- **API/schema**: no database migration and no API field changes.
