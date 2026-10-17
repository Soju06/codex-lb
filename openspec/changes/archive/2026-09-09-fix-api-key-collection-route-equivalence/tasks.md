## 1. Regression Coverage

- [x] 1.1 Add request-level GET and POST coverage for API-key collection URLs
  with and without a trailing slash
- [x] 1.2 Capture the current unslashed collection behavior failing the new
  regression without following redirects

## 2. Route Compatibility

- [x] 2.1 Register hidden unslashed aliases on the existing API-key collection
  handlers
- [x] 2.2 Verify canonical collection and detail route behavior remains
  unchanged

## 3. Verification

- [x] 3.1 Run focused API-key integration tests and changed-file static checks
- [x] 3.2 Validate OpenSpec and exercise slash/no-slash behavior through a live
  HTTP server
- [x] 3.3 Review the final diff and confirm no global routing or SPA fallback
  behavior changed
