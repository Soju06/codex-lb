## 1. Implementation

- [x] 1.1 Add a `reauth_required` account status and migration support.
- [x] 1.2 Map `token_invalidated`, `invalid_grant`, and
  session/refresh-token credential failures to `reauth_required`, while keeping `account_deactivated`,
  `account_suspended`, and `account_deleted` as `deactivated`.
- [x] 1.3 Exclude `reauth_required` accounts wherever paused/deactivated
  accounts are excluded from routing, active identity lookup, and account
  probing.
- [x] 1.4 Update dashboard/account status normalization, labels, filters, and
  re-authenticate actions for `reauth_required`.

## 2. Tests

- [x] 2.1 Add compact integration coverage for `401 token_invalidated` failing
  over to another account after the forced refresh retry.
- [x] 2.2 Add backend account/dashboard coverage that token invalidation persists
  and reports `reauth_required`, not `active` or `deactivated`.
- [x] 2.3 Add frontend coverage for the new account status label/filter/action
  behavior.

## 3. OpenSpec

- [x] 3.1 Add deltas for Responses auth failover, routing exclusion, and
  dashboard status display.
- [ ] 3.2 Validate the change and all specs locally when the OpenSpec CLI is
  available.
