## 1. Spec + Contracts

- [x] 1.1 Add delta spec for `admin-auth` user accounts + RBAC session model
- [x] 1.2 Add delta spec for `api-keys` ownership and mandatory `/backend-api/codex/*` key auth
- [x] 1.3 Add delta spec for `frontend-architecture` role-aware auth/navigation/management UX

## 2. Data Model + Migration

- [ ] 2.1 Add `dashboard_users` table and role enum (`admin`, `user`)
- [ ] 2.2 Add `owner_user_id` to `accounts` and backfill existing rows to default admin
- [ ] 2.3 Add `owner_user_id` to `api_keys` and backfill existing rows to default admin
- [ ] 2.4 Ensure default admin bootstrap (`admin` + `CODEX_LB_BOOTSTRAP_ADMIN_PASSWORD`) is idempotent

## 3. Backend RBAC + Ownership

- [ ] 3.1 Replace global password session contract with username/password user session principal
- [ ] 3.2 Add admin-only user/group management endpoints
- [ ] 3.3 Thread authenticated principal through dashboard contexts/services
- [ ] 3.4 Restrict `accounts`, `usage`, `request_logs`, `dashboard` views by owner for role `user`
- [ ] 3.5 Extend admin list endpoints with owner-user filtering where applicable
- [ ] 3.6 Enforce API key ownership:
  - key CRUD/list scoped by owner unless admin requests cross-user view
  - proxy account selection constrained by API key owner
- [ ] 3.7 Require API key for `/backend-api/codex/*` regardless of `api_key_auth_enabled`

## 4. Frontend

- [x] 4.1 Update auth schemas/api/store/login form to username+password session contract
- [x] 4.2 Add role-aware route/nav rendering (admin vs user)
- [x] 4.3 Add admin user-management UI (create/edit role/reset/deactivate)
- [x] 4.4 Add owner filters for admin where required and hide cross-user controls for user role

## 5. Tests (TDD)

- [ ] 5.1 Backend integration tests for login/session RBAC and user-scoped data access
- [ ] 5.2 Backend integration tests for API key ownership + mandatory `/backend-api/codex/*` key
- [ ] 5.3 Backend unit tests for new auth/session and ownership enforcement helpers
- [ ] 5.4 Frontend tests for username+password auth flow and role-gated UI

## 6. Verification

- [ ] 6.1 `uv run ruff check .`
- [ ] 6.2 `uv run ty check`
- [ ] 6.3 `uv run pytest tests/unit`
- [ ] 6.4 `uv run pytest tests/integration`
- [x] 6.5 `cd frontend && bun run lint`
- [x] 6.6 `cd frontend && bun run typecheck`
- [x] 6.7 `cd frontend && bun run test`
- [ ] 6.8 `openspec validate --specs`
