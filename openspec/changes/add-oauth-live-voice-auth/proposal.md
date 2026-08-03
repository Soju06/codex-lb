## Why

Codex-LB's private WebRTC Live Voice compatibility currently requires a registered Proxy API Key for both call creation and the sideband WebSocket. Current first-party Codex sends its existing ChatGPT OAuth bearer plus `chatgpt-account-id` on both legs and can route both legs through Codex-LB, but it cannot attach a separate Proxy API Key while retaining the built-in `openai` provider. Operators therefore must choose between official OAuth behavior and pooled Live Voice.

## What Changes

- Accept either an existing registered Proxy API Key or a verified ChatGPT OAuth caller on the four private Live Voice routes.
- Extract the existing Codex usage identity validation into one reusable, bounded, token-and-account-scoped resolver with short caching and singleflight.
- Resolve OAuth callers to an already imported per-seat Account identity and require an active OAuth Live policy with an explicit non-empty allowed-account set.
- Introduce a typed realtime caller scope so Key callers preserve every existing assignment, quota, request-log, and affinity behavior while OAuth callers use policy-scoped selection and nullable API-key attribution.
- Preserve the exact existing Key affinity digest input; add a separate OAuth affinity input derived from the internal caller Account id.
- Add policy persistence, dashboard APIs, and an Accounts-page editor without a new global setting or navigation item.
- Document the two first-party Codex route overrides required for current WebRTC Live Voice: call creation through `/backend-api/codex` and sideband through `/v1`.

## Capabilities

### New Capabilities

- None.

### Modified Capabilities

- `realtime-api-compat`: Private call creation and sideband accept a verified, policy-authorized OAuth caller in addition to a registered Proxy API Key.
- `account-identity`: Verified OAuth caller credentials map to one imported per-seat Account identity.
- `database-migrations`: OAuth Live policy and allowed-account relationships are represented by ORM metadata and one reversible Alembic revision.
- `frontend-architecture`: The existing Accounts page manages the selected caller account's OAuth Live policy and allowed upstream accounts.

## Impact

- Shared auth identity resolver and bounded in-process cache/singleflight.
- Two policy tables, repositories/services/schemas, dashboard API, and SQLite/PostgreSQL migration coverage.
- Live route authentication, exact-owner affinity, account selection, request logging, and Key compatibility regressions.
- Existing Accounts page API/hooks/components, translations, tests, and screenshots.
- OpenSpec, Live Voice documentation, focused Python/frontend suites, full local CI, and a real local WebRTC Live Voice acceptance run.

