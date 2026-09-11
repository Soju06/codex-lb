## 1. Permission vocabulary

- [x] 1.1 Add `Permission`, `Scope`, `ROLE_GRANTS`, `OWN_SCOPED_PERMISSIONS`, `PERMISSION_IMPLIES`, `PRIVILEGED_PERMISSIONS`, `legacy_permissions`, and `validate_grants` to `app/core/auth/dashboard_access.py`; validate the built-in table at import time.
- [x] 1.2 Extend `DashboardPrincipal` with `grants`, `scope()`, and `has()` while keeping `role`, `permissions`, and `can()` unchanged.
- [x] 1.3 Add `ensure_dashboard_permission`, `PermissionRequirement`, `DashboardPermissionDependency`, and the cached `require_dashboard_permission` factory to `app/core/auth/dependencies.py`; keep `require_dashboard_admin_access` / `ensure_dashboard_admin_access` as aliases for `conversations:read`.
- [x] 1.4 Add the optional `param` field to the dashboard error envelope and emit it from the dashboard domain-exception handler.

## 2. Route gates

- [x] 2.1 Gate the three account export routes with `accounts:export`.
- [x] 2.2 Gate `GET /api/audit-logs` with `audit:read`.
- [x] 2.3 Gate `/api/conversations*` and `/api/conversation-archive/*` with `conversations:read`; derive request-log `include_sensitive_metadata` and the `conversation_id` filter check from the same permission.
- [x] 2.4 Gate `/api/dashboard/overview`, `/api/dashboard/projections`, and `/api/usage/*` with `accounts:read`; gate `/api/models` with `dashboard:read`.
- [x] 2.5 Gate firewall rule mutations, guest-password set/remove, and upstream-proxy endpoint creation with `security:write`; require `security:write` in `PUT /api/settings` when the request sets a field in `SECURITY_SETTINGS_FIELDS`.

## 3. Verification

- [x] 3.1 Unit tests for the vocabulary: preset grants, alias derivation, scope ordering, `validate_grants` rules, privileged set, principal helpers, `permission_required` error shape, dependency caching.
- [x] 3.2 Integration tests proving each re-gated route rejects a principal that holds the `write` alias but lacks the specific permission, and that the `admin` preset is unaffected.
- [x] 3.3 Route authorization matrix test over the live app: session gate on every dashboard route, write-class gate on every mutation, exact permission on sensitive routes, no `own` requirement yet.
- [x] 3.4 Update existing tests from `admin_access_required` to `permission_required`.
- [x] 3.5 Run `ruff`, `ty`, focused pytest suites, and strict OpenSpec validation.

## 4. Documentation

- [x] 4.1 Add a "Roles and permissions" section to `docs/authentication.md` linking back to `openspec/specs/admin-auth/`.
- [x] 4.2 Update `docs/conversations.md` and add the `conversations-api` spec delta for the new error code.

## 5. Follow-ups (not in this change)

- [ ] 5.1 When `restrict-guest-audit-log-access` is archived, add a delta switching "Security audit reads require an admin principal" to `permission_required` / `audit:read`.
- [ ] 5.2 PR-0a-2 `restrict-guest-sensitive-surfaces`: guest exposure reductions and `guest_session_generation` (PLAN §6).
