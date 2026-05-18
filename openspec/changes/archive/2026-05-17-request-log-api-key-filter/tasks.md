## 1. Spec
- [x] 1.1 Add a frontend-architecture delta for request-log API key filtering.

## 2. Implementation
- [x] 2.1 Add request-log backend filtering by repeated `apiKeyId` query params.
- [x] 2.2 Expose API key filter options from the request-log options endpoint.
- [x] 2.3 Add the API key filter to dashboard request-log state, URL params, and UI.

## 3. Validation
- [x] 3.1 Add or update backend tests for request-log API key list/options filtering.
- [x] 3.2 Add or update frontend tests for request-log API key filter state and queries.
- [x] 3.3 Run targeted backend and frontend validation for the affected files.
- [ ] 3.4 Validate specs locally with `openspec validate --specs`.
