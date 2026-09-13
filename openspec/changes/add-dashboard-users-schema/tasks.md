## 1. Schema

- [x] 1.1 Add `DashboardUser` and `DashboardIdentity` models (string status/role_source, RESTRICT role FK, self FK for creator, unique identity triple, cascade identities).
- [x] 1.2 Add `api_keys.owner_user_id`, `created_by_user_id` (SET NULL) and `deactivated_reason`.
- [x] 1.3 Alembic revision `20260909_010000_add_dashboard_users` (guarded creates, batch-mode column adds with named FKs, downgrade).

## 2. Legacy admin bridge

- [x] 2.1 Backfill the `admin` user from `dashboard_settings` credentials in the migration (deterministic id, admin preset, break-glass designation, idempotent).
- [x] 2.2 `CompatAdminProjection` + `DashboardAuthRepository` mirroring for first-run setup, password set/clear, TOTP secret set/clear, and the replay counter (both-or-neither).
- [x] 2.3 Register the `dashboard_users` cache-invalidation namespace and log label.

## 3. Verification

- [x] 3.1 Integration: first-run setup creates the admin row; change/remove password mirrored; TOTP secret and counter mirrored; one-sided counter advance refused; identity uniqueness; API key ownership columns and SET NULL on owner deletion.
- [x] 3.2 Migration test with and without a legacy password: tables/columns created, backfill correct, downgrade clean, head reachable.
- [x] 3.3 `ruff`, `ty`, focused pytest, PostgreSQL drift contract, `openspec validate --strict`.
