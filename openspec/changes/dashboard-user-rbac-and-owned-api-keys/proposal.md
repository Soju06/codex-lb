## Why

The dashboard currently authenticates a single global admin password and does not model users, roles, or data ownership. This causes three gaps:

- No login-by-username account system with role separation (`admin`, `user`).
- No ownership boundary between operators when viewing/managing imported upstream accounts and API keys.
- `/backend-api/codex/*` can still be used without an API key when global key auth is disabled.

The requested behavior requires tenant-like ownership boundaries while preserving existing proxy and dashboard workflows.

## What Changes

- Introduce dashboard user accounts with role model (`admin`, `user`) and login/password authentication.
- Bootstrap a default admin user on startup:
  - `username`: `admin`
  - `password`: from `CODEX_LB_BOOTSTRAP_ADMIN_PASSWORD`
  - `role`: `admin`
- Store ownership on managed resources:
  - Add `owner_user_id` for imported upstream accounts.
  - Add `owner_user_id` for API keys.
- Enforce RBAC and ownership:
  - `admin`: full access + user/group management + cross-user visibility/filtering.
  - `user`: same feature set but only for own resources + own account settings.
- Require API key for all `/backend-api/codex/*` proxy calls regardless of global toggle.
- Scope proxy account selection by API key owner so a key only uses accounts owned by its user.

## Capabilities

### Modified Capabilities

- `admin-auth`: move from single global password mode to user-session + role-aware auth.
- `api-keys`: owner-scoped key lifecycle and `/backend-api/codex/*` mandatory key enforcement.
- `frontend-architecture`: username/password login UX, role-aware navigation, user management UI, and ownership filters.

## Impact

- **Backend**
  - `app/db/models.py`, new Alembic migration
  - `app/core/auth/dependencies.py`
  - `app/modules/dashboard_auth/*`
  - `app/modules/accounts/*`, `app/modules/dashboard/*`, `app/modules/usage/*`, `app/modules/request_logs/*`
  - `app/modules/api_keys/*`, `app/modules/proxy/*`
  - `app/dependencies.py`
- **Frontend**
  - `frontend/src/features/auth/*`
  - role-aware header/routes and settings/accounts/dashboard pages
  - user-management feature page and API client
- **Tests**
  - backend unit + integration coverage for RBAC, ownership filters, and key-required proxy behavior
  - frontend unit/integration updates for new auth contract and role-gated UI flows
