## Why

Dashboard authorization today is a single flat `write` permission. Renaming an account alias, disabling proxy API-key authentication, deleting a firewall rule, and exporting an upstream account's OAuth tokens are all authorized by the same check. Guest is the only other role, so every teammate who can change anything can change everything, and a future operator or member role would have nothing finer to be granted. The plan for per-user accounts and roles (`~/work/codex-lb/rbac-plan-0908/PLAN.md`, Phase 0 PR-0a) needs the permission vocabulary in place first, so later phases can add roles by editing one grant table instead of re-touching ~40 route gates.

## What Changes

- Introduce fine-grained dashboard permissions (`<resource>:<action>`) with an `all` / `own` scope, a fixed grant table for the built-in `admin` and `guest` roles, dependency rules between permissions, and a privileged-permission set. The existing `read` / `write` values stay as derived aliases so the session response contract is unchanged.
- Add a reusable `require_dashboard_permission(<permission>)` route dependency and `ensure_dashboard_permission(...)` helper. Denials return HTTP 403 with the stable code `permission_required` and name the missing permission in `param`.
- Re-gate sensitive routes by their own permission instead of the generic write gate or the ad-hoc admin gate:
  - account credential exports → `accounts:export`
  - audit log → `audit:read`
  - conversations and conversation archive → `conversations:read` (also drives request-log sensitive-metadata redaction)
  - security-bearing settings fields in `PUT /api/settings`, firewall rules, guest password, upstream-proxy endpoint credentials → `security:write`
  - dashboard overview / projections and `/api/usage/*` (account-window projections) → `accounts:read`; model catalog → `dashboard:read`
- Add a machine-checked route authorization matrix test that walks the live FastAPI routes: every dashboard route must validate the session, every mutation must declare a write-class gate, and the sensitive routes above must keep their exact permission.
- Dashboard error envelope gains an optional `param` field (already present on the OpenAI envelope).
- **BREAKING (error code only):** admin-only reads that returned `admin_access_required` now return `permission_required`, and the re-gated mutations (account exports, firewall rules, guest password, upstream-proxy endpoint creation) return `permission_required` instead of `read_only_access` to a guest. No client in this repository keys on either old code; the `read` / `write` session permissions and built-in admin/guest behavior are unchanged.

Behavior for the built-in roles is otherwise unchanged: `admin` holds every permission, `guest` holds `dashboard:read` and `accounts:read`.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `admin-auth`: Define the fine-grained permission vocabulary, the built-in role grants, the `permission_required` denial contract, the per-route permission requirements, and the machine-verified route matrix.

## Impact

- `app/core/auth/dashboard_access.py`, `app/core/auth/dependencies.py`: vocabulary, grant table, dependency factory.
- Route gates in `accounts`, `audit`, `conversation_archive`, `request_logs`, `dashboard`, `usage`, `firewall`, `settings`, `dashboard_auth` API modules; `SECURITY_SETTINGS_FIELDS` constant in `settings/schemas.py`.
- `app/core/errors.py` / `app/core/handlers/exceptions.py`: `param` on the dashboard error envelope.
- Tests: new unit vocabulary tests, new integration gate tests, new route matrix test; existing tests updated from `admin_access_required` to `permission_required`.
- No schema, migration, env var, README, `.env.example`, or dashboard nav change. Frontend is untouched (session `permissions` still emits only `read` / `write`).
