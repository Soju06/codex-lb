## Why

The APIs tab left sidebar shows each API key with pooled credit bars and, when limit rules exist, retains the legacy API Limit bar beneath them. This change ensures the list item continues to surface both the pooled status of the accounts assigned to each key and the key-specific limit usage, computed server-side by aggregating usage across the key's assigned accounts (or all accounts if unscoped), with pooled bars rendered identically to the account credit bars in the Accounts tab.

## What Changes

- Add `PooledCreditData` dataclass to the API keys service layer with `remaining_percent_primary`, `remaining_percent_secondary`, and `capacity_credits_primary` fields.
- Extend `list_keys()` to compute pooled credits per key by filtering `summarize_usage_window()` to the key's assigned accounts (or all accounts if none assigned).
- Add `pooled_remaining_percent_primary`, `pooled_remaining_percent_secondary`, and `pooled_capacity_credits_primary` to the `ApiKeyResponse` schema.
- Extract `MiniQuotaBar` from `account-list-item.tsx` into a shared component.
- Keep the legacy limit-usage bar in `api-list-item.tsx` beneath pooled credit bars labeled "Pooled 5h" and "Pooled Weekly" when limit rules exist.
- Hide the Pooled 5h bar when pooled primary capacity is 0.0 (e.g., all free-tier accounts).
- No reset countdown labels on pooled bars.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `api-keys`: API key list response now includes per-key pooled credit data computed from assigned accounts.
- `frontend-architecture`: API sidebar list items now render pooled credit bars instead of limit-usage bars. `MiniQuotaBar` is extracted to a shared component.

## Impact

- Backend: `app/modules/api_keys/{service,repository,api,schemas}.py`, `app/dependencies.py`
- Frontend: `frontend/src/components/mini-quota-bar.tsx` (new), `frontend/src/features/api-keys/schemas.ts`, `frontend/src/features/apis/components/api-list-item.tsx`, `frontend/src/features/accounts/components/account-list-item.tsx`
- Tests: Unit tests for `_compute_pooled_credits`, integration tests for pooled fields in list response, frontend schema tests
