## 1. Specs

- [x] 1.1 Add runtime version status API requirements.
- [x] 1.2 Add dashboard footer update-indicator requirements.

## 2. Backend

- [x] 2.1 Add a runtime version service that fetches and caches the latest GitHub release.
- [x] 2.2 Add `/api/runtime/version` response schema and route.
- [x] 2.3 Cover version comparison, cache behavior, and failure degradation.

## 3. Frontend

- [x] 3.1 Add runtime version API client/schema.
- [x] 3.2 Render a linked update icon beside the footer version only when an update is available.
- [x] 3.3 Cover footer link behavior and the no-update/no-error fallback.

## 4. Verification

- [x] 4.1 Run focused backend unit tests.
- [x] 4.2 Run focused frontend component tests.
- [x] 4.3 Validate OpenSpec specs.
