## 1. Schema

- [x] 1.1 `AuditLog` gains `actor_user_id`, `actor_username`, `actor_role_slug`, `auth_method`, `target_type`, `target_id`, `severity` plus indexes `idx_audit_logs_actor_user_id` and `idx_audit_logs_target`.
- [x] 1.2 Revision `20260909_030000_add_audit_actor_columns`: guarded column adds in SQLite batch mode, index creation, inspector-based downgrade; existing rows keep NULL actor columns and `severity='info'`; SQLite legacy `timestamp` text padded to the microsecond form.

## 2. Core types and sinks

- [x] 2.1 `app/core/audit/types.py`: `AuditSeverity`, `AuditAuthMethod`, `AuditActor` (`from_principal`), `AuditTarget`, `AuditEvent`.
- [x] 2.2 `AuditService.log_async` / `log` accept keyword-only `actor`, `target`, `severity`; positional parameters unchanged.
- [x] 2.3 `AuditSink` protocol, `DatabaseAuditSink`, registry (`get_audit_sinks`, `register_audit_sink`, `reset_audit_sinks`); `_write_audit_log` builds one event and fans out with per-sink error isolation; task ownership and drain unchanged.

## 3. Call sites

- [x] 3.1 Accounts, API keys, settings, model sources, quota planner, reset credits and guest revocation routes pass `actor=AuditActor.from_principal(principal)` and a target.
- [x] 3.2 Dashboard-auth service: `login_success` carries the account actor; `password_changed`, `password_removed`, `user_sessions_revoked`, `totp_enabled`, `totp_disabled` carry the account actor and `("user", id)` target; guest `login_success` carries the guest actor.
- [x] 3.3 `login_failed`: actor NULL, `severity=warning`, `details.reason` from the fixed vocabulary; `resolve_login_target` keeps the refusal reason; the login route audits `username_required` behind the password limiter's read-only check and a dedicated per-client audit budget.
- [x] 3.4 Self-service routes pass the session's `auth_method` to `change_password`, `remove_password`, `revoke_user_sessions`.

## 4. API

- [x] 4.1 `GET /api/audit-logs` filters `actor_user_id`, `target_type`, `target_id`, `severity`, `reason` (`^[a-z_]{1,32}$`, LIKE on the JSON text), `since` / `until` (UTC-normalised, inclusive / exclusive).
- [x] 4.2 `AuditLogResponse` gains `actor`, `target`, `severity`.

## 5. Verification

- [x] 5.1 Unit: actor mapping for the three principal kinds and `system`; event built once with sanitised details; failing sink does not block the database sink and is logged; existing task-ownership/drain tests updated to the event-based write signature.
- [x] 5.2 Integration: signed-in admin mutation attributed with target; implicit local admin recorded as `local_bootstrap`; failed sign-ins carry reason and warning without an actor (including `username_required`); listing filters and response shape; migration upgrade/downgrade on SQLite with a pre-existing row.
- [x] 5.3 `ruff`, `ty`, focused pytest, `openspec validate attribute-audit-actor --strict`.
