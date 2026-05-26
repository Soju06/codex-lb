## 1. Spec Backfill

- [x] 1.1 Add the API-key pooled credit computation and response fields requirement to `api-keys/spec.md`.
- [x] 1.2 Add the API sidebar pooled credit bar rendering requirement to `frontend-architecture/spec.md`.

## 2. Implementation

- [x] 2.1 Add `PooledCreditData` dataclass and `_compute_pooled_credits` helper to the API keys service layer.
- [x] 2.2 Compute pooled credits per key in `list_keys()` by filtering `summarize_usage_window()` to assigned accounts.
- [x] 2.3 Add pooled credit fields to `ApiKeyResponse` schema and `_to_response()` mapping.
- [x] 2.4 Add pooled credit fields to frontend `ApiKeySchema`.
- [x] 2.5 Extract `MiniQuotaBar` to shared component.
- [x] 2.6 Keep the legacy limit-usage bar in `api-list-item.tsx` beneath pooled credit bars when limit rules exist.

## 3. Verification

- [x] 3.1 Unit test `_compute_pooled_credits` for assigned accounts, all accounts, and free-tier capacity-zero cases.
- [x] 3.2 Integration test verifying pooled credit fields in `GET /api/api-keys/` response.
- [x] 3.3 Frontend schema test for new pooled fields.
- [x] 3.4 Validate the OpenSpec change locally.
