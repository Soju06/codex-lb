## 1. Vocabulary and registry

- [x] 1.1 Add `PresetRoleSlug`, `PRESET_ROLE_IDS` (UUIDv5), `PRESET_ROLE_NAMES`, `ASSIGNABLE_PRESET_ROLES`, and the operator/member/viewer grant tables; validate every preset at import.

## 2. Schema

- [x] 2.1 Add `DashboardRoleRecord` (`dashboard_roles`) and `DashboardRoleGrant` (`dashboard_role_grants`) models with string-typed `kind`/`permission`/`scope`.
- [x] 2.2 Add Alembic revision `20260909_000000_add_dashboard_roles` creating both tables (guarded, downgrade drops) and seeding the preset rows outside the guard.
- [x] 2.3 Shared idempotent seeder (`app/modules/dashboard_roles/seed.py`) used by the migration and `tests/conftest.py` (no startup hook).

## 3. Read model

- [x] 3.1 `DashboardRolesRepository` (list, by id, by slug, preset lookup) and `resolve_role_grants` / `grants_from_rows` (presets from code, custom from rows, unknown vocabulary ignored with a warning).

## 4. Verification

- [x] 4.1 Unit: preset tables match the plan, ids stable, guest not assignable, presets resolve from code even when rows exist, unknown vocabulary ignored.
- [x] 4.2 Integration: preset rows present after schema reset, seeding idempotent, custom grants round-trip with cascade delete, migration up/down/head.
- [x] 4.3 `ruff`, `ty`, focused pytest, `openspec validate --strict`.
