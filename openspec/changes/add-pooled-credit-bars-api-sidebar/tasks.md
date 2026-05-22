## 1. Spec Backfill

- [ ] 1.1 Add the API-key pooled credit computation and response fields requirement to `api-keys/spec.md`.
- [ ] 1.2 Add the API sidebar pooled credit bar rendering requirement to `frontend-architecture/spec.md`.

## 2. Implementation

- [ ] 2.1 Add `PooledCreditData` dataclass and `_compute_pooled_credits` helper to the API keys service layer.
- [ ] 2.2 Compute pooled credits per key in `list_keys()` by filtering `summarize_usage_window()` to assigned accounts.
- [ ] 2.3 Add pooled credit fields to `ApiKeyResponse` schema and `_to_response()` mapping.
- [ ] 2.4 Add pooled credit fields to frontend `ApiKeySchema`.
- [ ] 2.5 Extract `MiniQuotaBar` to shared component.
- [ ] 2.6 Replace limit-usage bar in `api-list-item.tsx` with pooled credit bars.

## 3. Verification

- [ ] 3.1 Unit test `_compute_pooled_credits` for assigned accounts, all accounts, and free-tier capacity-zero cases.
- [ ] 3.2 Integration test verifying pooled credit fields in `GET /api/api-keys/` response.
- [ ] 3.3 Frontend schema test for new pooled fields.
- [ ] 3.4 Validate the OpenSpec change locally.
