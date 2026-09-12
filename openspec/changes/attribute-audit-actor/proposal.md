## Why

Audit rows today say *what* happened (`action`), from *where* (`actor_ip`) and a free-form `details` blob. They do not say *who* did it: the previous change (`user-login-and-session-v2`) made `dashboard_users` the source of truth for sign-in, so every dashboard request now carries an account (`user_id`, `username`, `role_slug`, `auth_method`) on its principal, but none of that reaches the audit table. Once several people share an install, "settings changed from 10.0.0.5" is not an answer. The listing also cannot be filtered by anything but `action`, and every write goes straight to the database, so a future external sink (webhook / SIEM, PLAN §4.7) would have to touch every call site.

## What Changes

- **Attribution columns on `audit_logs`** (revision `20260909_030000_add_audit_actor_columns`): `actor_user_id`, `actor_username`, `actor_role_slug`, `auth_method`, `target_type`, `target_id` (all nullable) and `severity` (`info|warning|critical`, NOT NULL, default `info`). The actor columns are a snapshot, deliberately without a foreign key, so an audit row outlives the account it names. Existing rows keep NULL actor/target columns and get `severity='info'`.
- **`AuditActor` / `AuditTarget` / `AuditSeverity`** in `app/core/audit/types.py`; `AuditActor.from_principal(principal)` maps the three principal kinds (account, implicit admin — keeping the trusted-header asserted username — and guest). `AuditService.log_async()` / `log()` gain keyword-only `actor`, `target`, `severity`; the positional parameters are unchanged so existing callers compile.
- **Every dashboard call site passes its actor** (accounts, API keys, settings, model sources, quota planner, reset credits, guest revocation, password/TOTP self-service). `login_success` names the account that signed in; `login_failed` stays actor-less, becomes `severity=warning`, and always records `details.reason` from `{bad_password, bad_totp, unknown_identity, disabled_user, invalid_username, username_required}` — including the `422 username_required` refusal, which was not audited before.
- **Sink interface.** `AuditSink` protocol, `DatabaseAuditSink` (the current row write), and a module-level registry (`get_audit_sinks` / `register_audit_sink` / `reset_audit_sinks`) defaulting to the database sink. The write task builds one `AuditEvent` and fans it out; a failing sink is logged and never blocks the others. Task ownership and shutdown drain are unchanged. No webhook sink in this change.
- **Listing filters** on `GET /api/audit-logs` (`audit:read`): `actor_user_id`, `target_type`, `target_id`, `severity`, `reason`, `since`, `until` next to the existing `action`/`limit`/`offset`. Each row gains `actor {userId, username, roleSlug, authMethod} | null`, `target {type, id} | null`, `severity`.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `audit-logging`: rows record actor, auth method, target and severity; writes fan out to registered sinks with the database sink authoritative; listing supports actor/target/severity/reason/time filters; failed sign-ins record a reason.

## Impact

- `app/core/audit/{types,service}.py`, `app/db/models.py` (`AuditLog`), one Alembic revision, `app/modules/audit/{api,repository,service,schemas}.py`, and the audit call sites in `app/modules/{accounts,api_keys,settings,model_sources,quota_planner,rate_limit_reset_credits,dashboard_auth}`.
- Wire: additive response fields and query parameters only; `audit_logs` stays append-only (no UPDATE/DELETE path). No new setting, env var, README section, nav item or frontend change (the dashboard has no audit view yet).
