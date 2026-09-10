## Why

A failed refresh token does not prove that the stored access token is unusable. Treating `reauth_required` as globally unavailable discards valid request capacity and breaks owner-bound continuity before upstream rejects the access token.

## What Changes

- Define one canonical status baseline: `active` and unexpired `reauth_required` accounts are request-routable; known-expired, paused, and deactivated accounts are not.
- Keep proactive refresh disabled for `reauth_required` while allowing ordinary access-token-authenticated operations until known access-token expiry.
- Preserve owner-bound affinity while the stored access token remains unexpired, then reject reuse locally without crossing hard owners.
- After permanent forced-refresh failure, exclude the account only from the current request's remaining movable retries.
- Reconcile fresh account state before claimless forced refresh so peer rotations are adopted and unchanged terminal material is not exchanged.

## Capabilities

### Modified Capabilities

- `account-routing`: Own request routability, known-expiry quiescing, continuity, and explicit all-expired-pool errors.
- `usage-refresh-policy`: Separate refresh eligibility from request eligibility and define safe claimless forced-refresh reconciliation.

## Impact

The policy affects selectors, refresh handling, affinity, probes, warmup, automations, usage/reset-credit surfaces, API-key pools, and dashboard capacity projections. It adds no setting, schema change, migration, dependency, or setup step.
