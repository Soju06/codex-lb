## Context

`AuditService.log_async()` is called from about 30 dashboard routes and service methods. Since `user-login-and-session-v2` every request principal carries the account behind it, but the audit row only ever stored `action`, `actor_ip`, `details`, `request_id`. This change adds attribution without changing the fire-and-forget write model (owned tasks, shutdown drain) that `audit-logging` already specifies.

## Goals / Non-Goals

**Goals**
- Every audit row written from a dashboard request names the acting account (or the kind of implicit principal), how it authenticated, and where possible the object it acted on.
- Failed sign-ins are searchable by reason.
- The write path has a seam for a second destination that call sites never see.

**Non-Goals**
- A webhook/SIEM sink (Phase 5a), audit UI, retention/purge, editing or deleting rows, new settings.

## Decisions

### Actor is a snapshot, not a foreign key

`actor_user_id` is a plain `String(36)` with an index, and `actor_username` / `actor_role_slug` are copied at write time. A foreign key with `ON DELETE SET NULL` would erase the one thing an investigator needs after an account is removed, and `CASCADE` would delete evidence. The snapshot also keeps the row honest about *which* role the person held at the time, which a join to a later-edited role could not.

### Three principal kinds, one mapping

`AuditActor.from_principal` is the only place that knows how a `DashboardPrincipal` becomes an actor: an account session copies `user_id/username/role_slug/auth_method`; the implicit admin (local bootstrap, trusted header, disabled auth) has no account, so it records `role_slug=admin` and its `auth_method` (`local_bootstrap|trusted_header|disabled`) with NULL id and username; a guest records `role_slug=guest`, `auth_method=guest`. Service methods that receive a `DashboardUser` instead of a principal (password change/removal, session revocation, TOTP) build the actor from the user row with the method that authenticated the session; the TOTP path snapshots it before the counter write because a refused replay rolls the session back and expires the instance.

The `AuditAuthMethod` vocabulary already lists `cli` for a future command-line writer; no constructor exists for it until a caller does.

### `login_failed` has no actor but always a reason

The party that failed to sign in is by definition unauthenticated, so the actor columns stay NULL and the identifying data lives in `details` (`method`, `username`, `reason`). `resolve_login_target` keeps the refusal reason (`unknown_identity`, `disabled_user`, `invalid_username`) on the target so `verify_user_password` can record it while the client still receives one indistinguishable `401 invalid_credentials` after exactly one bcrypt check. `username_required` (422, no rate-limit budget) is now audited too, but from the route, not the service: because the refusal happens before the password limiter by design, an anonymous client on a multi-user install could otherwise append audit rows without bound. The route writes the row only if the client is not already at the password limit (the limiter's non-incrementing `check`) and a dedicated `login_failed_audit` limiter (same 8/60 s shape, its own counter) still admits it; the password limiter's counter is never touched by these requests. Severity `warning` makes the failures filterable without a reason list.

`AuditActor.from_principal` records the proxy-asserted username (`DashboardRequestAuth.actor`) for the trusted-header admin, so those rows are not anonymous even though no account row exists.

### Sinks: build once, fan out, database first

`_write_audit_log` receives a fully built `AuditEvent` (sanitised details, UTC timestamp, resolved request id) and awaits each registered sink in order inside its own `try`. The database sink is registered first and is the authoritative record; a failing sink is logged with its class name and the action and the loop continues. The registry is a module-level tuple (`get_audit_sinks` / `register_audit_sink` / `reset_audit_sinks`) rather than a setting: the only consumer today is tests, and the webhook change will decide how a sink is configured. `_AUDIT_LOG_TASKS`, `drain_audit_log_tasks` and the admission cutoff are untouched.

The event timestamp moves from the database default (`func.now()`) to the process clock (`datetime.now(UTC)`) so every sink sees the same instant; the row is written with that value.

### `reason` filter is a text match on the JSON column

`details` is `Text`, written by `json.dumps` with default separators, so a `reason` filter is `details LIKE '%"reason": "<value>"%'` with `_` escaped. This is portable across SQLite and PostgreSQL and needs no JSON operator or generated column, at the cost of a table scan bounded by the other predicates (`action`, `severity`, time window) and of relying on the writer's spelling. The value is validated against `^[a-z_]{1,32}$` before it reaches SQL. If the audit log grows into a hot query path this becomes a real column; today the listing is admin-only and paged.

### Time window

`since` is inclusive, `until` exclusive. Both are normalised to UTC (naive input is taken as UTC) because rows are stored with UTC wall-clock components and SQLite compares the text form.

On SQLite that text form differs between rows written through the database default (`CURRENT_TIMESTAMP`, `YYYY-MM-DD HH:MM:SS`) and rows the ORM writes or binds (`.ffffff` suffix), so an inclusive `since` on an exact legacy second would skip legacy rows and same-second ordering would interleave wrongly. The migration pads legacy SQLite rows once (`timestamp || '.000000'` where no fraction is present). This changes the representation only, never the instant; PostgreSQL stores a real `timestamptz` and is untouched. The model keeps its `func.now()` default.

### Severity vocabulary

`info` (default), `warning` (failed sign-ins), `critical` (reserved for break-glass and destructive identity events in later changes). Stored as a string with a `StrEnum` in code, like every other vocabulary column in this schema.

## Size

The change lands at roughly +560 net lines of application code against the ~450 planned; the overrun is the new `app/core/audit/types.py` module (the actor/target/event vocabulary the sink interface needs) and the migration with its guards, not the call-site threading.

## Risks / Trade-offs

- Every audited route now reads its principal by name (`principal: DashboardPrincipal = Depends(...)`) instead of an ignored `_write_access` parameter; tests that call route functions directly must pass a principal.
- The `reason` LIKE filter is only as good as the writers' spelling; the vocabulary is fixed in the spec and tested end to end.
- Actor snapshots go stale on username change by design; the id stays stable.
