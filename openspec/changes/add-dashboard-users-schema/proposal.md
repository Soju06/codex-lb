## Why

The dashboard has no notion of a person: the shared admin password is two columns on the settings row, TOTP is one secret for the whole install, and API keys have no owner. Per-user accounts need their tables before any login, invite, or audit change can use them. This change lands the schema and the migration bridge for the existing shared password, with no visible behaviour change.

## What Changes

- New tables `dashboard_users` (username, display name, e-mail, `role_id` → `dashboard_roles` RESTRICT, `role_source`, `status`, per-user password hash and TOTP secret/step, `session_generation`, `must_change_password`, `is_break_glass`, audit timestamps, `created_by_user_id`) and `dashboard_identities` (external identities, unique on provider/provider_key/subject, cascade on user deletion).
- `api_keys` gains `owner_user_id` and `created_by_user_id` (both SET NULL on user deletion) and `deactivated_reason`; existing keys are unowned "service keys".
- Alembic revision `20260909_010000_add_dashboard_users` creates the above and **backfills the legacy admin**: if `dashboard_settings.password_hash` is set, an `admin` user (deterministic id, admin preset, `is_break_glass=true`) is created with the same password hash, TOTP secret, and replay counter. Installs without a password get no user; first-run password setup creates the `admin` row at runtime.
- **Expand/contract release N**: the legacy `dashboard_settings` credential columns stay as a projection of the `admin` user. Every legacy credential write (first-run setup, password change/removal, TOTP secret set/clear, TOTP replay-counter advance) is mirrored onto the user row in the same transaction; the replay counter must advance on both rows or the code is rejected as a replay. No code path reads the user row yet, so admin/guest behaviour is identical; the next change switches reads to the users table.
- `dashboard_users` cache-invalidation namespace registered (consumer arrives with the users cache).

## Capabilities

### New Capabilities

- `dashboard-users`: account and identity schema, API key ownership columns, legacy-admin backfill, and the release-N compat projection.

### Modified Capabilities

None (the `admin-auth` login/session contract is unchanged in this change).

## Impact

- `app/db/models.py`, one Alembic revision, `app/modules/dashboard_users/compat.py`, `app/modules/dashboard_auth/repository.py` (mirroring), `app/core/cache/invalidation.py` (namespace).
- No route, setting, env var, README, nav, or frontend change. Existing tests unchanged; new integration tests cover backfill, mirroring, replay refusal, uniqueness, and ownership columns.
