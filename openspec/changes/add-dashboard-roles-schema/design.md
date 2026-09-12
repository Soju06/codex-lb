## Context

The RBAC plan (D7) fixes five preset roles and lets administrators clone them into custom roles later. Users, invites, identity mappings, key policies, and providers will all carry a `role_id`, so roles must be rows. The permission vocabulary and scope model already exist in `app/core/auth/dashboard_access.py`.

## Goals / Non-Goals

**Goals**
- Give every future role reference one foreign-key target with stable ids across installs and replicas.
- Keep preset grants out of the database so an upgrade never has to reconcile rows and mixed-version fleets cannot disagree about what `admin` means.
- Load custom-role grants tolerantly during rolling upgrades.
- Zero behaviour change for existing installs.

**Non-Goals**
- `dashboard_roles.created_by_user_id`: deferred to the first writer of custom roles (the custom-role editor), where it can carry the FK to `dashboard_users`; presets have no author.
- Any API, UI, or principal change (users arrive in the next change; the role editor in Phase 5).
- Enforcing dependency rules on stored custom grants at read time (the editor enforces them at write time; the loader only drops unknown vocabulary).

## Decisions

### Preset rows, code grants

`dashboard_roles` rows for presets are inserted once and never updated; `kind='preset'` rows have no `dashboard_role_grants`. `resolve_role_grants` returns `PRESET_ROLE_GRANTS[slug]` for presets and the stored rows for custom roles. A boot-time reconciler that rewrites grants was rejected: an older replica rebooting would strip newer grants, and concurrent boots race outside the migration lock.

### Deterministic ids

Preset ids are UUIDv5 of the slug under a fixed namespace (`PRESET_ROLE_IDS`). Migrations, fixtures, and future data (mappings, policies) can reference them without a lookup, and two databases seeded independently agree.

### Seeding is shared and idempotent

`seed_preset_dashboard_roles(connection)` uses dialect-specific "insert … on conflict do nothing" (PostgreSQL, SQLite) with a select-then-insert fallback. The migration calls it outside its table-existence guard (re-run safe) with a revision-pinned table definition, and the test schema reset calls it after `create_all` because tests do not run Alembic. There is deliberately no startup hook: it would run outside `migration_lock` and outside `init_db`'s fail-fast policy (a schema-less process with `CODEX_LB_DATABASE_MIGRATIONS_FAIL_FAST=false` would crash on the INSERT), and the migration already guarantees the rows on every supported path.

### Plain string columns, application-validated

`kind` (`RoleKind`), `permission`, and `scope` are `String` columns validated by `StrEnum`s in code, not `SqlEnum`, so adding a value never needs a database type migration (the same rule the plan sets for users' `status`/`role_source`).

### Tolerant loader

`grants_from_rows` skips rows whose permission or scope is not in this replica's vocabulary and logs them, instead of raising. During a rolling upgrade a newer replica may have written a permission this one does not know; failing every request for the role's holders would be worse than temporarily ignoring the new grant.

## Risks / Trade-offs

- [Risk] A future change creates a database without migrations (e.g. a CLI) and forgets the presets. → Seeding is one function; the only supported non-Alembic path (the test schema reset) calls it, and a missing preset surfaces as an FK error at the first user insert rather than as a quiet default.
- [Risk] `guest` as a row could be assigned to a user by mistake. → `assignable_to_users=false` on the row and `ASSIGNABLE_PRESET_ROLES` in code; the users change enforces it.
- [Trade-off] Preset display names/descriptions are seeded in English and never updated by later migrations. → UI renders presets via i18n keyed by slug; the row text is a fallback for API consumers.
