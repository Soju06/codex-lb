## Why

Dashboard mutations are authenticated by a `SameSite=Lax` session cookie, or by nothing at all on a loopback deployment without a password. Neither stops a page on another site from making the operator's browser send a state-changing request to the dashboard: `SameSite=Lax` is a browser default that some clients relax, and the loopback no-password mode has no cookie to protect in the first place. There is no CSRF token, no origin check, and no CORS policy today, so `POST /api/dashboard-auth/logout`, `PUT /api/settings`, account deletion, and every other mutation are reachable cross-site.

On the client side the auth store boots with `role: "admin"`, `permissions: ["read", "write"]`, `canWrite: true` and the gate renders the application before the session request has even started. Admins see a frame that later re-renders; guests briefly see admin-only controls; a session response that omits `role` or `permissions` is treated as an admin. The RBAC plan (`~/work/codex-lb/rbac-plan-0908/PLAN.md`, hardening items H3 and H4, Phase 0 PR-0b) requires both problems fixed before user accounts and finer roles ship.

## What Changes

- Add a pure-ASGI middleware that rejects cross-site browser mutations under `/api/` before routing and before any authentication dependency runs. It trusts the browser-controlled `Sec-Fetch-Site` header first, falls back to comparing `Origin` with the request's own scheme, host, and port, and lets requests that carry neither header through unchanged (non-browser clients). Bearer-authenticated data-plane routes (`/api/fleet/`, `/api/codex/`) and any request with an `Authorization: Bearer` header are exempt. Rejections answer `403` with the dashboard envelope and error code `cross_site_request_rejected`.
- Zero configuration: no new setting, no allowlist. The expected origin is derived from the request itself.
- Add a live-route test proving every non-safe `/api/` route (including `/api/dashboard-auth/*` and `/logout`) rejects a `Sec-Fetch-Site: cross-site` request with the new code.
- Frontend: the auth store starts and resets on logout with `role: "guest"`, `permissions: []`, `canWrite: false`; the auth gate renders only the spinner until the session response has been applied; the session schema defaults `role` to `guest` and `permissions` to `[]`, and accepts any permission string so future fine-grained values do not reject the whole session.
- **BREAKING (browser behavior only):** a browser page hosted on a different origin can no longer trigger dashboard mutations, and a dashboard served behind a reverse proxy that rewrites the `Host` header to the upstream address must forward the browser's original `Host` including its port (nginx `proxy_set_header Host $http_host;`, Apache `ProxyPreserveHost On`; Traefik and Caddy do so by default) for `Origin`-based checks to match. Non-browser clients (curl, httpx, scripts) are unaffected because they send neither header.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `admin-auth`: Define the cross-site rejection rules, the exemptions, and the error contract for dashboard mutations.
- `frontend-architecture`: Define the least-privilege client boot state, the auth gate hold, and tolerant permission parsing; update the conversations fail-closed scenario to the new default role.

## Impact

- `app/core/middleware/dashboard_csrf.py` (new), `app/core/middleware/__init__.py`, `app/main.py` (registration).
- `frontend/src/features/auth/hooks/use-auth.ts`, `frontend/src/features/auth/components/auth-gate.tsx`, `frontend/src/features/auth/schemas.ts`.
- Tests: new unit middleware tests, new integration tests on the real app, one new case in the dashboard route matrix, updated frontend store/schema/gate/page tests.
- `docs/authentication.md`: new "Cross-site request protection" paragraph.
- No schema, migration, env var, README, `.env.example`, nav, or i18n change.
