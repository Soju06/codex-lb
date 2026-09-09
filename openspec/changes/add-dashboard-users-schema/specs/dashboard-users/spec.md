## ADDED Requirements

### Requirement: Dashboard user and identity tables

The system SHALL store dashboard accounts in `dashboard_users` with `id`, unique `username`, `display_name`, unique nullable `email`, `role_id` referencing `dashboard_roles` with RESTRICT deletion, string `role_source` (`manual`, `mapping`, `scim`; default `manual`), string `status` (`active`, `disabled`, `invited`; default `active`), nullable `password_hash`, nullable `totp_secret_encrypted` and `totp_last_verified_step`, `session_generation` (default 0), `must_change_password` and `is_break_glass` (default false), `created_at`, nullable self-referencing `created_by_user_id` (SET NULL), `updated_at`, and nullable `last_login_at`. External identities SHALL be stored in `dashboard_identities` with `user_id` cascading on user deletion and a unique `(provider, provider_key, subject)` triple. `api_keys` SHALL carry nullable `owner_user_id` and `created_by_user_id` (SET NULL on user deletion) and a nullable string `deactivated_reason`.

#### Scenario: Duplicate identity is rejected

- **WHEN** a second identity row with the same provider, provider key, and subject is inserted
- **THEN** the insert fails with a uniqueness violation

#### Scenario: Deleting a user detaches keys and removes identities

- **WHEN** a user who owns an API key and has an identity is deleted
- **THEN** the key remains with `owner_user_id` null
- **AND** the identity row is removed

### Requirement: Legacy shared admin is migrated into the admin user

The Alembic revision `20260909_010000_add_dashboard_users` MUST create the tables and columns above and, when `dashboard_settings.password_hash` is set, MUST insert a user with the deterministic compat id, username `admin`, the admin preset role, `status` `active`, `role_source` `manual`, `is_break_glass` true, and the same `password_hash`, `totp_secret_encrypted`, and `totp_last_verified_step`. When no legacy password is set the revision MUST create no user. The backfill MUST be idempotent on re-run and the downgrade MUST drop the new tables and columns.

#### Scenario: Install with a password gets one admin user

- **GIVEN** a database at the parent revision whose settings row has a password hash, TOTP secret, and replay step
- **WHEN** the revision is applied
- **THEN** exactly one `dashboard_users` row exists with username `admin`, the admin preset, break-glass designation, and the copied credential fields

#### Scenario: Install without a password gets no user

- **GIVEN** a settings row without a password hash
- **WHEN** the revision is applied
- **THEN** `dashboard_users` is empty

### Requirement: Legacy credential columns are a projection of the admin user

During the expand/contract release, every write to the legacy dashboard credential MUST be mirrored onto the `admin` user row within the same transaction: first-run password setup MUST create the `admin` user (deterministic id, admin preset, break-glass designation); password change and removal MUST update or clear the user's `password_hash`; TOTP secret set and clear MUST update the user's secret and reset its replay step; password removal MUST also clear the user's TOTP fields. Advancing the TOTP replay counter MUST succeed only if both the legacy column and the user row advance; otherwise nothing is written and the code is rejected as a replay. Authentication and session reads MUST continue to use the legacy columns in this release.

#### Scenario: First-run setup creates the admin account

- **WHEN** `POST /api/dashboard-auth/password/setup` succeeds on an install with no users
- **THEN** a `dashboard_users` row `admin` exists with the same password hash as the legacy column
- **AND** a second setup attempt is refused and no second user is created

#### Scenario: Password change is mirrored

- **WHEN** the dashboard password is changed
- **THEN** the `admin` user's `password_hash` equals the legacy column

#### Scenario: One-sided replay counter advance is refused

- **GIVEN** the `admin` user's replay step is already at the submitted step while the legacy column is behind
- **WHEN** the replay counter is advanced with that step
- **THEN** the call reports a replay and neither row changes
