## 1. Fleet freshness contract

- [x] 1.1 Specify the independent OAuth and quota-snapshot timestamps.
- [x] 1.2 Propagate the newest loaded usage sample timestamp into the fleet
  summary without adding a query.
- [x] 1.3 Keep usage freshness hidden when upstream quota visibility is denied.

## 2. Regression coverage

- [x] 2.1 Cover newest-sample selection and missing usage in the account mapper.
- [x] 2.2 Cover the fleet response shape, quota-visibility behavior, and the
  unchanged `/api/accounts` response contract.
- [x] 2.3 Prove force probe and fleet refresh advance `usageRefreshedAt` without
  changing `lastRefreshAt` when credentials are not refreshed.

## 3. Verification

- [x] 3.1 Run focused unit and integration tests.
- [x] 3.2 Run Ruff check/format, focused ty, and strict OpenSpec validation.
