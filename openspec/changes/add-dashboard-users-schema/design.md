## Context

The RBAC plan turns the shared password into an `admin` user without a visible migration step (D11): same password, same TOTP, no banner. Replicas of the previous release must keep working during a rolling upgrade, and they read only the legacy `dashboard_settings` columns. The role rows this schema references were added by `add-dashboard-roles-schema`.

## Goals / Non-Goals

**Goals**
- Land every account-related column once (users, identities, key ownership) so later changes never re-migrate.
- Migrate the existing credential into the `admin` user automatically and idempotently.
- Keep old and new replicas consistent for one release by mirroring writes, never by dual reads.

**Non-Goals**
- Reading users for authentication, per-user TOTP enrolment, invites, or any API (next changes).
- Stamping `created_by_user_id` on new API keys (principals carry no user id yet).
- Removing the legacy columns (release N+1).

## Decisions

### Backfill in the migration, create at runtime for fresh installs

The revision inserts the `admin` row only when a legacy password exists; a passwordless install creates it at first `POST /password/setup` through `CompatAdminProjection.ensure_exists`. Both use the same deterministic id and the unique username, so re-running the migration or racing the bootstrap cannot produce two admins.

### Write-side projection, read-side untouched

`DashboardAuthRepository` mirrors password/TOTP writes to the user row **before** the legacy write inside the same session and commits once; the retry-on-version-conflict path re-applies the legacy mutation only (the user mirror is not versioned). New code does not read the user row: introducing `users OR legacy` reads now would create a second truth to unwind in N+1, so the switch happens atomically in the next change.

### Replay counter advances on both rows or neither

`try_advance_totp_last_verified_step` runs both conditional updates and rolls back unless both advanced (a missing compat user simply means there is nothing to mirror). A code accepted by the legacy column but already consumed on the user row — possible when a newer replica verified it — is therefore rejected as a replay on both, closing T16.

### Plain string status/role_source columns

As with roles, `status` and `role_source` are `String` columns validated by `str`/`Enum` classes in code, so `invited`, `mapping`, `scim` and future values need no type migration.

### Ownership columns now, semantics later

`owner_user_id`, `created_by_user_id`, and `deactivated_reason` are nullable and unused; adding them here avoids a second `api_keys` ALTER (SQLite table rebuild) when self-service keys arrive.

## Risks / Trade-offs

- [Risk] Migration backfill and runtime creation race on a fresh install during rolling upgrade. → Deterministic id + unique username make the second insert fail idempotently; the runtime path checks existence first.
- [Risk] A future refactor writes the legacy column without going through the repository. → All legacy credential writes already funnel through `DashboardAuthRepository`; the mirror lives there.
- [Trade-off] The compat user's `password_hash` mirror is not covered by the optimistic version of `dashboard_settings`. → Acceptable for one release: both writes happen in one transaction from one request, and the next change makes the user row authoritative.
