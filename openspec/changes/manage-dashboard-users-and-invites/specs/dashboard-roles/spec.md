## ADDED Requirements

### Requirement: Roles read API

`GET /api/dashboard-roles` SHALL require `users:manage` and SHALL list every role row as `{id, slug, name, description, kind, locked, assignableToUsers, grants: [{permission, scope}], usersCount}` where `locked` is true for `kind == "preset"`, `grants` are resolved with `resolve_role_grants` (code for presets, rows for custom roles), `assignableToUsers` for presets is the code truth (`admin`, `operator`, `viewer` this release) regardless of the row, and `usersCount` counts accounts holding the role.

#### Scenario: Preset listing

- **WHEN** an admin lists roles
- **THEN** the five presets are returned with `locked: true`, `guest` and `member` are not assignable, and the admin role's grants contain every permission at `all`

### Requirement: Permission descriptors

`GET /api/dashboard-roles/permissions` SHALL require `users:manage` and SHALL return one entry per `Permission` as `{permission, description, implies, ownSupported, privileged}` derived from `PERMISSION_IMPLIES`, `OWN_SCOPED_PERMISSIONS`, `PRIVILEGED_PERMISSIONS`, and a description table that MUST cover every permission (unit-tested).

#### Scenario: Vocabulary

- **WHEN** an admin reads the permission descriptors
- **THEN** `api_keys:assign` implies `api_keys:write`, `api_keys:write` supports `own`, `users:manage` is privileged, and every entry has a description
