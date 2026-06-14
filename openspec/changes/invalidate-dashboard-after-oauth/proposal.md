## Why

After an operator adds an account through OAuth, the Accounts page and Dashboard can otherwise keep showing stale cached data until the next background refetch. Operators need the newly added account to appear promptly in both account management and dashboard summary surfaces.

## What Changes

- Define the cache invalidation contract for successful OAuth account creation.
- Require both browser/manual callback completion and device-code completion paths to invalidate account-list and dashboard query data.
- Keep the backend OAuth API contract unchanged.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `query-caching`: OAuth account creation now defines which query caches must be invalidated after successful completion.

## Impact

- Frontend OAuth hook cache invalidation in `frontend/src/features/accounts/hooks/use-oauth.ts`
- Shared account-related query invalidation helper in `frontend/src/features/accounts/query-invalidation.ts`
- Frontend OAuth/account hook tests
