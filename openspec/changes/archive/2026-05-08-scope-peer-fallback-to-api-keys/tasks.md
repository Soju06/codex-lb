## 1. Persistence And Contracts

- [x] 1.1 Add the `api_key_peer_fallback_urls` ORM model and Alembic migration.
- [x] 1.2 Extend API key service data contracts with `peer_fallback_base_urls`.
- [x] 1.3 Add repository operations to validate and replace API key peer fallback URLs.

## 2. Runtime Behavior

- [x] 2.1 Resolve peer fallback candidates from authenticated API key peer fallback URLs only.
- [x] 2.2 Pass `ApiKeyData` into stream and buffered peer fallback paths.
- [x] 2.3 Remove global registered/env peer targets from runtime default selection.

## 3. Dashboard

- [x] 3.1 Extend frontend API key schemas with `peerFallbackBaseUrls`.
- [x] 3.2 Add peer fallback URL list controls to API key create and edit dialogs.
- [x] 3.3 Update mock handlers and factories for the new API key field.
- [x] 3.4 Remove Settings peer fallback catalog selection from the API key flow.

## 4. Verification

- [x] 4.1 Add or update backend tests for API key peer fallback URL CRUD and validation.
- [x] 4.2 Add or update peer fallback runtime tests for per-key URL selection and no-URL disablement.
- [x] 4.3 Add or update frontend tests for API key peer fallback URL payloads.
- [x] 4.4 Run OpenSpec, backend, and frontend relevant checks.
- [x] 4.5 Add frontend coverage for direct peer fallback URL entry from API key dialogs.
