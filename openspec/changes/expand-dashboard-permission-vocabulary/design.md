## Context

`DashboardPrincipal` carries `role ∈ {admin, guest}` and `permissions ⊆ {read, write}`. Routers gate reads with `validate_dashboard_session`, mutations with `require_dashboard_write_access`, and two routers (conversations, conversation archive, audit) with `require_dashboard_admin_access`, which is a role check rather than a permission check. Secret-bearing reads (account credential exports) are gated by `write` because no finer permission exists. The RBAC plan needs `operator`, `member`, `viewer`, and custom roles later; each of those is a different subset of a vocabulary that does not exist yet.

## Goals / Non-Goals

**Goals**

- One fine-grained vocabulary (`Permission`) and one scope axis (`Scope = all | own`) that later roles are expressed in, without touching route code again.
- Built-in `admin` / `guest` behavior unchanged, except that sensitive routes now fail with a permission-specific error.
- A dependency that any router can use (`require_dashboard_permission`) and a helper for in-handler checks (`ensure_dashboard_permission`).
- A CI test that reads the live route table so a missing or regressed gate fails the build.
- Session response contract unchanged, so the React client and its zod schema keep working.

**Non-Goals**

- New roles, user accounts, custom roles, or `own`-scoped enforcement (no owner column exists yet; the matrix test asserts no route requires `own`).
- Guest data-exposure reductions (planned as the next hardening PR).
- Replacing the ~40 `require_dashboard_write_access` call sites. The alias stays valid; specific permissions replace it incrementally where it matters.

## Decisions

### Grants are a mapping `Permission → Scope`, aliases are derived

`ROLE_GRANTS[role]` is an immutable mapping. `DashboardPrincipal.grants` holds it; `permissions` (the legacy `read` / `write` set) is computed by `legacy_permissions(grants)`:

- `read` ⇔ `dashboard:read` granted at any scope.
- `write` ⇔ `accounts:write`, `api_keys:write`, and `ops:write` all granted at `all` scope — because the generic write gate protects mutations in all three areas, a principal missing any of them must not pass it.

Alternative rejected: storing both sets independently on the principal. Two sources of truth would drift the moment a custom role is introduced.

### `own` scope is declared now, enforced later

Only `dashboard:read`, `api_keys:read`, `api_keys:write` may be granted with `own`. `validate_grants()` rejects `own` elsewhere and enforces `PERMISSION_IMPLIES` (`api_keys:assign ⇒ api_keys:write`, `accounts:export ⇒ accounts:read`, `security:write ⇒ ops:write`) at import time for the built-in table and, later, for any editor input. Route dependencies accept `minimum_scope`, and `all` satisfies `own`. Nothing in this change requires `own`; the matrix test pins that so ownership filtering is designed deliberately when owners exist.

### Denials are `permission_required` + `param`

`ensure_dashboard_permission` raises `DashboardPermissionError(code="permission_required", param=<permission>)`. The dashboard error envelope gains an optional `param` (mirroring the OpenAI envelope) so clients can show which permission is missing. The previous `admin_access_required` code encoded a role, which will be wrong as soon as a non-admin role can hold `conversations:read`; keeping it would freeze the role model into the API. `read_only_access` from the generic write gate is unchanged.

### The dependency is a cached callable object

`require_dashboard_permission(permission, minimum_scope=...)` is `lru_cache`d and returns a `DashboardPermissionDependency` instance. FastAPI resolves it via `__call__`; the route matrix test reads `.requirement` without invoking it. One object per requirement keeps `dependency_overrides` and OpenAPI stable. The old `require_dashboard_admin_access` / `ensure_dashboard_admin_access` remain as thin aliases for `conversations:read` so external forks keep importing.

### `PUT /api/settings` splits security fields from operational fields

The endpoint mixes both. `SECURITY_SETTINGS_FIELDS` (`totp_required_on_login`, `api_key_auth_enabled`, `guest_access_enabled`, `dashboard_session_ttl_seconds`, `hide_upstream_quota_from_api_keys`) lives beside the request model; the handler requires `security:write` only when the request **changes** one of them — a non-null value that differs from the stored setting. Field presence is not enough: the dashboard client (`frontend/src/features/settings/payload.ts`) spreads the full current settings into every save, so a presence check would make every settings save a security write. Splitting the endpoint was rejected because that same full-form client behavior makes a second endpoint a contract change for no gain.

### Account-window projections require `accounts:read`, not `dashboard:read`

`/api/dashboard/overview`, `/api/dashboard/projections`, and `/api/usage/*` are computed from upstream account quota windows and have no API-key dimension. A future `member` with only `dashboard:read own` cannot be served by filtering them, so they are gated as account reads now. Reports, request logs, and key trends are classified under `dashboard:read` in the vocabulary because they derive from request-log rollups that already carry `api_key_id`; in this change their routers still rely on the router-level session gate (which every built-in role passes), and the explicit `dashboard:read` dependency is added when a role that lacks it exists.

### Route matrix is a live-app test, not a static table

`tests/integration/test_dashboard_route_permission_matrix.py` walks `app.routes` and each route's dependant tree. Rules: every `/api/` route validates the session (exempt: `/api/fleet/*` and `/api/codex/*` which use proxy API keys, and `/api/dashboard-auth/*` which issues sessions — except the guest-password mutations, which must carry `security:write`); every non-safe method has the write alias or a non-read permission; the listed sensitive routes carry exactly their permission. A static table alone was rejected because it cannot notice a *new* unguarded route.

## Risks / Trade-offs

- [Risk] A client keyed on `admin_access_required`. → Searched the frontend (no usage); documented as an error-code-only breaking change in the proposal and release notes.
- [Risk] `param` on the dashboard envelope surprises a strict client. → It is additive and only present when set; `DashboardErrorDetail.param` is `NotRequired`.
- [Risk] The write alias derivation is stricter than "any write permission". → Intentional: the generic gate must not admit a partial writer. Covered by `test_legacy_aliases_are_derived_from_grants`.
- [Trade-off] Two implemented-but-unarchived changes still specify `admin_access_required`: `restrict-guest-sensitive-request-data` (its `admin-auth` and `proxy-runtime-observability` deltas) and `restrict-guest-audit-log-access` (its `admin-auth` delta adds "Security audit reads require an admin principal"). This delta's MODIFIED blocks target the current main-spec text, as the validator requires, and already fold in the guest-denial scenarios those changes introduced, with the error code updated to `permission_required`. Archive order: archive those two changes first, then this one; when this change archives, the "Security audit reads require an admin principal" block must be updated to `permission_required` / `audit:read` (a follow-up delta, since the requirement does not exist in the main spec yet).
- [Risk] Request-conditional checks (`PUT /api/settings` security fields, request-log `conversation_id`) are invisible to the route matrix because they live inside handlers. → Covered by behavioral tests in `test_dashboard_permission_gates.py`; a registry for two call sites would be over-engineering.
