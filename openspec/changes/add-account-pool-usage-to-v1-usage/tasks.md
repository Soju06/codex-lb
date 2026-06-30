## 1. Database Migration

- [x] 1.1 Add `usage_sections` TEXT column to `api_keys` table with default `"upstream_limits,account_pool_usage"`
- [x] 1.2 Create Alembic migration file with idempotent column check and batch_alter_table pattern

## 2. Backend Model

- [x] 2.1 Add `usage_sections` Mapped[str | None] column to ApiKey model in `app/db/models.py`

## 3. Backend Dataclasses

- [x] 3.1 Add `usage_sections: str = "upstream_limits,account_pool_usage"` to `ApiKeyCreateData`
- [x] 3.2 Add `usage_sections: str | None = None` and `usage_sections_set: bool = False` to `ApiKeyUpdateData`
- [x] 3.3 Add `usage_sections: str = "upstream_limits,account_pool_usage"` to `ApiKeyData`

## 4. Backend API Schemas

- [x] 4.1 Add `usage_sections: str | None = None` to `ApiKeyCreateRequest` in `app/modules/api_keys/schemas.py`
- [x] 4.2 Add `usage_sections: str | None = None` to `ApiKeyUpdateRequest` in `app/modules/api_keys/schemas.py`
- [x] 4.3 Add `usage_sections: str = "upstream_limits,account_pool_usage"` to `ApiKeyResponse`
- [x] 4.4 Add `AccountPoolUsageResponse` class and `account_pool_usage: AccountPoolUsageResponse | None = None` to `V1UsageResponse` in `app/modules/proxy/schemas.py`

## 5. Backend Service Layer

- [x] 5.1 Handle `usage_sections` in `create_key` — pass `payload.usage_sections` to ApiKey row
- [x] 5.2 Handle `usage_sections` in `update_key` — pass to repository update when set
- [x] 5.3 Add `usage_sections` to `_to_api_key_data` and `_to_created_data` converters

## 6. Backend API Handlers

- [x] 6.1 Pass `usage_sections` from `ApiKeyCreateRequest` to `ApiKeyCreateData` in `create_api_key` handler
- [x] 6.2 Handle `usage_sections` in `update_api_key` handler — set `usage_sections_set` and pass value
- [x] 6.3 Add `usage_sections` to `_to_response` in `app/modules/api_keys/api.py`
- [x] 6.4 Validate `usage_sections` values (only allow `upstream_limits`, `account_pool_usage`) on create/update

## 7. /v1/usage Handler Changes

- [x] 7.1 Compute `account_pool_usage` by reusing `_compute_pooled_credits` or inline computation in `/v1/usage` handler
- [x] 7.2 Parse `usage_sections` from the authenticated API key in the handler
- [x] 7.3 Conditionally include `account_pool_usage` and `upstream_limits` based on parsed sections

## 8. Frontend Schemas

- [x] 8.1 Add `usageSections` string field to `ApiKeySchema`, `ApiKeyCreateRequestSchema`, `ApiKeyUpdateRequestSchema`

## 9. Frontend UI Components

- [x] 9.1 Add "Usage sections shown to client" multi-select dropdown below "Assigned accounts" in `api-key-create-dialog.tsx`
- [x] 9.2 Add "Usage sections shown to client" multi-select dropdown below "Assigned accounts" in `api-key-edit-dialog.tsx`

## 10. Tests

- [x] 10.1 Add unit tests for `usage_sections` in `test_api_keys_service.py`
- [x] 10.2 Add unit tests for `usage_sections` in `test_api_keys_repository.py`
- [x] 10.3 Add integration tests for `/v1/usage` with `account_pool_usage` in `test_v1_usage.py`
- [x] 10.4 Add integration tests for API key create/update with `usage_sections` in `test_api_keys_api.py`
- [x] 10.5 Add frontend schema tests for `usageSections` in `schemas.test.ts`
- [x] 10.6 Add frontend component tests for usage sections dropdown
- [x] 10.7 Verify all existing tests pass
