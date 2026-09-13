## ADDED Requirements

### Requirement: Dashboard roles are rows with stable preset identities

The system SHALL store dashboard roles in a `dashboard_roles` table with columns `id`, `slug` (unique), `name`, `description`, `kind` (`preset` or `custom`), `assignable_to_users`, `cloned_from_role_id`, `permissions_version`, `created_at`, and `updated_at`. Five preset rows MUST exist with slugs `admin`, `operator`, `member`, `viewer`, and `guest`, `kind` `preset`, and ids equal to the UUIDv5 derived from the slug under the fixed preset namespace, so every install and replica uses identical identifiers. The `guest` preset MUST have `assignable_to_users` false; the other presets true. The preset rows MUST be seeded idempotently by the Alembic revision (re-run safe, insert-only) and by the test schema reset; the application MUST NOT rewrite preset rows at startup.

#### Scenario: Fresh database has the five presets

- **WHEN** the schema is created and seeded
- **THEN** `dashboard_roles` contains exactly the five preset slugs with `kind` `preset` and their deterministic ids
- **AND** repeating the seed does not add or modify rows

#### Scenario: Guest cannot be assigned

- **WHEN** the preset rows are read
- **THEN** only `guest` has `assignable_to_users` false

### Requirement: Preset grants live in code, custom grants in rows

The system SHALL keep the grant table of every preset role in code (`PRESET_ROLE_GRANTS`) and MUST NOT store preset grants in the database; `dashboard_role_grants` rows exist only for custom roles. Resolving a preset role's grants MUST return the code table regardless of any rows attached to the preset. The preset grant tables MUST be: `admin` every permission at `all`; `operator` `dashboard:read`, `accounts:read`, `accounts:write`, `api_keys:read`, `api_keys:write`, `api_keys:assign`, `ops:write` at `all`; `member` `dashboard:read`, `api_keys:read`, `api_keys:write` at `own`; `viewer` and `guest` `dashboard:read` and `accounts:read` at `all`. Every preset table MUST satisfy the vocabulary scope and dependency rules.

#### Scenario: Upgrade adds a permission to presets without a data change

- **WHEN** a release adds a permission to the vocabulary and to a preset's code table
- **THEN** the preset resolves to the new grant set with no migration or row update

#### Scenario: Custom role resolves from its rows

- **WHEN** a `custom` role has grant rows `dashboard:read all` and `api_keys:read own`
- **THEN** resolving it yields exactly those grants

### Requirement: Custom grant loading tolerates unknown vocabulary

When loading `dashboard_role_grants` rows, the system MUST skip any row whose `permission` or `scope` is not in this process's vocabulary, log the skipped row, and return the remaining grants, rather than failing the request.

#### Scenario: Newer replica wrote an unknown permission

- **WHEN** a custom role has grant rows `dashboard:read all` and `future:permission all`
- **THEN** resolving it on a replica without `future:permission` yields `dashboard:read all` only
- **AND** a warning naming the role and permission is logged

### Requirement: Dashboard roles migration

The Alembic revision `20260909_000000_add_dashboard_roles` MUST create `dashboard_roles` and `dashboard_role_grants` (with `role_id` cascading on role deletion) when absent, seed the preset rows even on re-run, and drop both tables on downgrade.

#### Scenario: Upgrade, downgrade, head

- **GIVEN** a database at the parent revision
- **WHEN** the revision is applied
- **THEN** both tables exist, five preset rows are present, and no grant rows exist
- **AND** downgrading removes both tables
- **AND** upgrading to head passes through the revision on a single-head graph
