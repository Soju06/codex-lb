## Why

Per-user dashboard accounts (the next change) need a role to point at, and later changes (identity mappings, invites, key policies, custom roles) all reference roles by foreign key. The permission vocabulary already exists in code (`expand-dashboard-permission-vocabulary`); what is missing is a durable role identity in the database. Storing preset grants in the database would create a second source of truth that drifts across replica versions, so the schema stores preset *rows* only and keeps preset grants in code.

## What Changes

- New tables `dashboard_roles` (id, slug, name, description, kind `preset|custom`, assignable_to_users, cloned_from_role_id, permissions_version, timestamps) and `dashboard_role_grants` (role_id, permission, scope) — one Alembic revision that also seeds the five preset rows (`admin`, `operator`, `member`, `viewer`, `guest`) with stable UUIDv5 ids and zero grant rows.
- Code-defined grant tables for every preset (`PRESET_ROLE_GRANTS`; operator/member/viewer added next to the existing admin/guest), a preset registry (`PresetRoleSlug`, `PRESET_ROLE_IDS`, `ASSIGNABLE_PRESET_ROLES`), and a read-only `dashboard_roles` module (repository + `resolve_role_grants`) that resolves presets from code and custom roles from their grant rows, ignoring unknown permission strings.
- Idempotent, insert-only preset seeding shared by the migration and the test schema reset (no startup reconciler).
- No consumer changes: no route reads the new tables yet, the principal model is untouched, guest and admin behave exactly as before.

## Capabilities

### New Capabilities

- `dashboard-roles`: Role identity rows, preset locking (grants in code), custom-role grant storage, and the tolerant grant loader.

### Modified Capabilities

None.

## Impact

- `app/db/models.py` (two models), `app/db/alembic/versions/20260909_000000_add_dashboard_roles.py`, `app/core/auth/dashboard_access.py` (preset registry + grant tables), new `app/modules/dashboard_roles/`, `app/main.py` (seed after `init_db`), `tests/conftest.py` (seed after `create_all`).
- No setting, env var, README, nav, or API change.
