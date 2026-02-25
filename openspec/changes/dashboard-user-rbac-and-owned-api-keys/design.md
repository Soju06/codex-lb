## Overview

This change introduces first-class dashboard users and ownership boundaries while preserving existing module layering.

Key architectural goals:

- Keep request-scoped dependency injection and typed service/repository contracts.
- Avoid mixing auth principal parsing into business services.
- Enforce ownership both in dashboard APIs and proxy account selection.

## Data Model

### New table: `dashboard_users`

- `id` (string/uuid PK)
- `username` (unique, canonical login identifier)
- `password_hash` (bcrypt hash)
- `role` (`admin` | `user`)
- `is_active` (soft-disable account)
- timestamps

### Ownership fields

- `accounts.owner_user_id` (not null after backfill)
- `api_keys.owner_user_id` (not null after backfill)

Backfill strategy:

- Create/find default admin user row first.
- Assign existing account and api-key rows to that admin user.

## Auth Model

Session cookie payload extends current encrypted contract with principal fields:

- `uid`: user id
- `un`: username snapshot
- `ur`: role snapshot
- Existing `exp`, `pw`, `tv` remain for compatibility with current TOTP/password flows.

Guard behavior:

- Dashboard guard requires a valid user session.
- Principal is attached to request context via typed dependency.
- Admin-only routes use explicit role-check dependency (fail with dashboard auth envelope).

## API / Service Layer Changes

- Introduce user repository/service for CRUD and role updates (admin-only API surface).
- Update dashboard auth login request to include `username` + `password`.
- Add principal-aware filters to repositories:
  - by owner for `accounts`, `usage`, `request_logs`, `dashboard`.
  - API keys list/create/update/delete scoped by owner unless caller is admin with explicit filter.
- Proxy auth:
  - `/backend-api/codex/*` uses strict API-key dependency (always required).
  - validated key carries `owner_user_id`.
  - load balancer selection receives owner scope and picks only matching accounts.

## Frontend

- Auth state stores current user principal (`id`, `username`, `role`).
- Login form collects username + password.
- Header/routes render role-appropriate navigation:
  - `admin`: full tabs + user management controls.
  - `user`: own resources only.
- Admin pages expose owner filters for cross-user list views.

## Failure Modes / Controls

- Missing default admin bootstrap: fail-fast startup error with explicit message.
- Inactive user login attempts: reject with `invalid_credentials` (no user existence leak).
- API key owner without active accounts: proxy returns existing `no_accounts` flow.
- Cross-owner resource access attempts: return `404` for object routes, filtered-empty for list routes, and `403` on admin-only routes.
