## Why

`POST /api/accounts/{id}/reactivate` already returns a `deactivated` account to
`active` and clears its `deactivation_reason`. The service guard is explicit about
this: it refuses only `reauth_required`, where the stored refresh token really is
unusable. Deactivation itself is reached for permanent failures *outside* that
set, so an account can be deactivated by a cause that has since gone away — a
transient upstream or OAuth interruption, for example.

The dashboard never surfaces that endpoint for `deactivated`. All three account
surfaces gate their resume control on `status === "paused"`, and for
`deactivated` they offer only re-authentication. An operator whose account was
deactivated by a cause that no longer applies has to either re-run the full OAuth
flow or edit the database by hand, even though the supported API call does exactly
what they need.

## What Changes

- Offer the existing resume action for `deactivated` accounts on the accounts page,
  the dashboard account list, and the dashboard account card.
- Keep re-authentication available on those accounts, so a genuinely expired
  credential still has its own path.
- Leave `reauth_required` with re-authentication only, because the reactivate
  endpoint refuses that status and a resume control there could only produce a
  conflict.
- Add regression coverage for both branches.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `frontend-architecture`: Require the dashboard to offer the reactivate action for
  deactivated accounts while withholding it for accounts that require
  re-authentication.

## Impact

- Affected dashboard components:
  `frontend/src/features/accounts/components/account-actions.tsx`,
  `frontend/src/features/dashboard/components/account-list.tsx`, and
  `frontend/src/features/dashboard/components/account-card.tsx`.
- Affected tests: account actions component tests.
- No API, schema, persistence, dependency, or configuration changes: the endpoint,
  its state guard, and the existing resume mutation are all unchanged.
