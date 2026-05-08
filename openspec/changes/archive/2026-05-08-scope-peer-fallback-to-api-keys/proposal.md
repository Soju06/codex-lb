## Why

Peer fallback targets are currently resolved as a global runtime pool. Operators need fallback routing to be scoped per API key so each key can fail over only to peer `codex-lb` URLs configured on that API key.

## What Changes

- Add API key owned peer fallback URL storage.
- Expose peer fallback base URLs in API key create, update, list, and regenerate responses.
- Change runtime peer fallback so it only attempts URLs configured on the authenticated API key.
- Disable runtime fallback for unauthenticated requests or API keys with no peer fallback URLs.
- Remove peer fallback selection/catalog UI from Settings.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `api-keys`: API keys can own an ordered list of peer fallback base URLs.
- `peer-fallback-targets`: Registered peer fallback targets are no longer a global runtime fallback pool.
- `frontend-architecture`: API key management exposes direct peer fallback URL entry.
- `database-migrations`: Alembic creates API key peer fallback URL storage.
- `proxy-runtime-observability`: Runtime peer fallback eligibility is scoped to authenticated API key URLs.

## Impact

- Database schema: new `api_key_peer_fallback_urls` table.
- Backend API: API key payloads gain `peerFallbackBaseUrls`.
- Runtime proxy: peer fallback functions receive the authenticated API key policy.
- Frontend: API key create/edit forms include direct peer fallback URL entry.
- Tests: API key CRUD, peer fallback runtime, API key dialog, and migration coverage need updates.
