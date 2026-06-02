## Why

API key clients calling `/v1/usage` cannot see pooled account capacity usage. They only see their own key-level limits and aggregate upstream credit windows, but not the actual remaining percent of primary/secondary account pool capacity. Additionally, API key administrators cannot control which usage sections are exposed to clients — all sections are always returned.

## What Changes

- Add `account_pool_usage` object to `GET /v1/usage` response with `primary` (float) and `secondary` (float) remaining_percent values
- Add a `usage_sections` field to the API key model (TEXT, comma-separated values) that controls which sections are returned in `/v1/usage`
- Add a multi-select dropdown in the API key create/edit UI labeled "Usage sections shown to client" with options `upstream_limits` and `account_pool_usage`, placed below "Assigned accounts"
- Default `usage_sections` to `"upstream_limits,account_pool_usage"` (select all) via migration
- `/v1/usage` response conditionally includes `account_pool_usage` and `upstream_limits` based on the key's `usage_sections` setting

## Capabilities

### New Capabilities

- `account-pool-usage-v1-usage`: Expose pooled account remaining percentages on `/v1/usage` and allow API key admins to control which usage detail sections are visible to clients

### Modified Capabilities

- `api-keys`: API key model gains `usage_sections` TEXT field; create/update endpoints accept `usage_sections`; frontend forms include usage sections multi-select; `/v1/usage` response schema gains `account_pool_usage` and honors `usage_sections` filtering

## Impact

- **Database**: New `usage_sections` TEXT column on `api_keys` table (migration)
- **Backend**: `ApiKey` model, `ApiKeyCreateRequest`/`ApiKeyUpdateRequest`/`ApiKeyResponse` schemas, `ApiKeysService.create_key`/`update_key`, `GET /v1/usage` handler, `V1UsageResponse` schema
- **Frontend**: `ApiKeySchema`, `ApiKeyCreateRequestSchema`, `ApiKeyUpdateRequestSchema`, `api-key-create-dialog.tsx`, `api-key-edit-dialog.tsx`
- **No breaking changes**: New field defaults to both sections enabled, preserving existing behavior
