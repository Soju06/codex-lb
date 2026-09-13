## ADDED Requirements

### Requirement: The users table is the authentication source of truth

From this change on, `dashboard_users` SHALL be the only source the system reads for dashboard credentials and account state: password hashes, TOTP secrets and replay counters, `status`, and `session_generation`. The legacy `dashboard_settings` credential columns (`password_hash`, `totp_secret_encrypted`, `totp_last_verified_step`) SHALL be a write-only mirror of the compat `admin` account: every credential write to that account MUST update the legacy columns in the same transaction (password set, password/TOTP clear, TOTP secret set/clear, replay counter advance — which MUST succeed on both rows or on neither), and no other account MUST ever be mirrored. First-run password setup MUST create the compat `admin` account, or re-arm an existing credential-less one, only when no active account can already sign in; that decision MUST be taken from `dashboard_users` alone, and the legacy columns MUST then be overwritten with the new credential (a stale legacy hash left by a previous-release replica MUST NOT block setup).

#### Scenario: Compat admin writes reach both rows

- **WHEN** the `admin` account changes its password
- **THEN** `dashboard_users.password_hash` and `dashboard_settings.password_hash` hold the same new hash

#### Scenario: Other accounts are not mirrored

- **WHEN** an account other than `admin` sets a TOTP secret
- **THEN** `dashboard_settings.totp_secret_encrypted` is unchanged

#### Scenario: Stale legacy hash does not block setup

- **GIVEN** `dashboard_settings.password_hash` is set and no account exists
- **WHEN** `POST /api/dashboard-auth/password/setup` is called
- **THEN** the `admin` account is created, the legacy column holds the new hash, and the new password signs in

#### Scenario: Setup refused while an account can sign in

- **GIVEN** an active account holds a password
- **WHEN** `POST /api/dashboard-auth/password/setup` is called
- **THEN** the response is `409 password_already_configured` and no account is created

### Requirement: Legacy credentials are re-projected once before the switch

Alembic revision `20260909_020000_reproject_compat_admin_credentials` (parent `20260909_010000_add_dashboard_users`) MUST run before the user row becomes authoritative and MUST copy the legacy `dashboard_settings` credential onto the compat `admin` row: when the legacy `password_hash` is set, the `admin` row MUST exist (created with the deterministic compat id, admin preset, `is_break_glass` true when missing) and its `password_hash`, `totp_secret_encrypted`, and `totp_last_verified_step` MUST equal the legacy values, overwriting whatever the row held; when the legacy `password_hash` is NULL and the row exists, those three columns MUST be set to NULL without deleting the row. `session_generation` MUST NOT change. When `dashboard_settings` has no row the revision MUST do nothing. The downgrade MUST be a no-op.

#### Scenario: Stale user row is overwritten

- **GIVEN** the legacy row holds a newer password hash and TOTP step than the `admin` row
- **WHEN** the revision is applied
- **THEN** the `admin` row carries the legacy values and its `session_generation` is unchanged

#### Scenario: Removed legacy password clears the row

- **GIVEN** the legacy `password_hash` is NULL and an `admin` row exists with credentials
- **WHEN** the revision is applied
- **THEN** the row still exists with NULL password hash, TOTP secret, and replay step

#### Scenario: No settings row

- **GIVEN** `dashboard_settings` is empty
- **WHEN** the revision is applied
- **THEN** `dashboard_users` is unchanged
