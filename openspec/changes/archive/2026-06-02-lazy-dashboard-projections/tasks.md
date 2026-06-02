## 1. Implementation

- [x] Split projection fields out of the overview backend path.
- [x] Add an authenticated `/api/dashboard/projections` endpoint.
- [x] Fetch projections from the dashboard SPA after overview data is present.
- [x] Use projection payload data for safe-line and weekly-credit views while preserving
  fallback behavior for older overview responses.

## 2. Verification

- [x] Cover projection endpoint behavior in dashboard integration tests.
- [x] Cover frontend schemas, hooks, utilities, and mocks.
- [x] Run targeted backend and frontend validation.
