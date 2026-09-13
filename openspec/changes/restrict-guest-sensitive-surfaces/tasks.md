## 1. Guest-restricted reads

- [x] 1.1 Gate `GET /api/api-keys*` (list, trends, usage-7d) with `api_keys:read` at router level.
- [x] 1.2 Gate `GET /api/settings/upstream-proxy`, `GET /api/settings/runtime/connect-address`, `GET /api/sticky-sessions` with `ops:write`; gate `GET /api/oauth/status` with `accounts:write`.
- [x] 1.3 Redact account identity (`email` masked; `chatgptAccountId`, `workspaceId`, `workspaceLabel` null) for principals without `accounts:write` in `GET /api/accounts` and `GET /api/dashboard/overview` via `build_account_summaries(redact_identity=...)`.
- [x] 1.4 Exclude `Account.email` from request-log free-text search for principals without `accounts:write` (flag threaded through service/repository and the count cache key); without `api_keys:read`, return `apiKeys: []` from `GET /api/request-logs/options`, null `apiKeyId`/`apiKeyName` on request-log rows, and exclude key ids/names from search.

## 2. Generation-bound guest sessions

- [x] 2.1 Add `dashboard_settings.guest_session_generation` (model + Alembic revision `20260908_000000_add_guest_session_generation`, idempotent up/down).
- [x] 2.2 Carry `gg` in guest session cookies; validate equality in `validate_dashboard_session`, `DashboardAuthService.get_session_state`, and the `/session` guest branch; reject non-integer values; legacy guest cookies without `gg` never match.
- [x] 2.3 Bump on guest password set/clear (auth repository), on guest access on→off (settings repository), and via `POST /api/dashboard-auth/guest/logout-all` (`security:write`, audit `guest_sessions_revoked`).

## 3. Verification

- [x] 3.1 Unit: session store round-trips `gg`, requires it for guest sessions, rejects non-integer values, leaves admin sessions without it.
- [x] 3.2 Integration: guest sessions die after password rotation, password removal, guest access off→on, and logout-all; unrelated settings saves keep them; logout-all requires `security:write`.
- [x] 3.3 Integration: guest sees masked identity, admin sees full identity; guest search cannot match e-mails; guest options omit API keys; guest gets `permission_required` on the gated reads while aggregate reads still work.
- [x] 3.4 Migration up/down test and PostgreSQL drift contract; route matrix expectations extended.
- [x] 3.5 `ruff`, `ty`, focused pytest suites, `openspec validate --strict`.

## 4. Documentation

- [x] 4.1 Update `docs/authentication.md` "Roles and permissions" (what a guest can read, masked identity, guest logout-all).
