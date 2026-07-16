# Design — document-replica-topology-contract

## 1. New capability vs. extending deployment-installation

`replica-operations` is a new capability because the contract spans deployment, scheduling,
caching, metrics, and crypto; `deployment-installation` is Helm/data-dir mechanics and none of
its existing requirement headers fit. Sibling in-flight changes (harden-scheduler-leader-election,
extend-cache-invalidation-bus, serialize-cross-replica-token-refresh) own their mechanism-level
deltas; replica-operations states topology-level prerequisites (WHAT an operator must provision)
and references those capabilities rather than duplicating lease/bus mechanics, so the wording
survives the sibling fixes. Only testable requirements go in `spec.md`; known limitations,
rationale, and runbooks go in `context.md`.

## 2. Encryption-key fingerprint check

New table `runtime_sentinels(name VARCHAR(64) PK, value VARCHAR(128) NOT NULL, created_at,
updated_at NULL)`; row `name='encryption_key_fingerprint'`, `value='sha256:<hex of raw Fernet
key bytes>'`. Startup hook (`app/core/config/key_fingerprint.py`, wired in lifespan right after
`init_db()` and before `ensure_auto_bootstrap_token()` which already exercises the encryptor):
compute local fingerprint; atomic `INSERT .. ON CONFLICT DO NOTHING` using the
pg_insert/sqlite_insert dialect pattern; re-SELECT; on mismatch, behavior per new setting
`encryption_key_fingerprint_mode` (`enforce`|`warn`|`off`, default `enforce`). Enforce raises a
RuntimeError naming both fingerprint prefixes and remediation. Backend behavior: PostgreSQL — PK
uniqueness under MVCC arbitrates the first-boot race (exactly one insert wins, the loser reads
the winner's row and compares); SQLite — the single-writer lock makes insert-or-noop atomic. No
advisory lock needed because insert-if-absent is itself the arbitration.

Rejected: encrypt-a-known-constant sentinel (Fernet ciphertexts are salted, so it becomes
decrypt-verify — equivalent guarantee, more code); default `warn` (a divergent key already means
replica-dependent auth failures; failing fast with remediation is strictly clearer, and
warn/off are the escape hatches).

## 3. PUT /api/settings optimistic locking

`dashboard_settings.version INTEGER NOT NULL server_default '1'` with
`__mapper_args__ = {"version_id_col": version}` on `DashboardSettings`. SQLAlchemy emits every
ORM flush as `UPDATE .. SET version = :new WHERE id = 1 AND version = :old` and raises
`StaleDataError` on rowcount 0 — identical semantics on asyncpg and aiosqlite. **Correction
found during verification:** an earlier draft claimed the CAS binds the version read at request
start (`service.get_settings()`); it does not — the session identity map holds clean rows by
weak reference, and `get_settings()` converts the row to a dataclass and drops it, so the ORM
row is collected and the update path's own `get_or_create()` re-reads fresh. The CAS therefore
binds the version read by `service.update_settings()` itself: two writers whose read→commit
windows interleave (the cross-replica race) still resolve to exactly one winner, and the loser's
UPDATE matches 0 rows → `StaleDataError` → mapped to 409 `settings_conflict` via
`DashboardSettingsConflictError` raised from `SettingsRepository.commit_refresh`. Binding all
the way back to form-load time is exactly what the optional `expectedVersion` payload field is
for (checked against `current.version` before any write; omitting it preserves today's request
shape, so no breaking change; frontend adoption is a follow-up). The response schema gains
`version`.

Side effect handled: `version_id_col` makes all ORM writers of `dashboard_settings`
conflict-sensitive. The dashboard-auth single-field writers (password/TOTP/guest-password
mutations) are idempotent absolute writes, so they get a retry-once-on-conflict wrapper
(`DashboardAuthRepository._mutate_settings_with_retry`) — previously they silently lost updates,
now they re-read and re-apply. `store_bootstrap_token_if_absent` / `clear_bootstrap_token` /
`try_set_password_hash` use Core `UPDATE` statements with their own WHERE guards; they bypass
ORM versioning and stay atomic, so they need no retry (they do not bump `version`, which is
acceptable: the version guards the full-row read-modify-write path, and those writers touch
credential fields the settings PUT never writes).

Rejected: `updated_at` CAS (SQLite timestamp resolution allows equal-timestamp races);
`pg_advisory_xact_lock` (no SQLite analogue and cannot span the handler's read→validate→write
awaits); PATCH-only model_fields_set rewrite (larger diff, still loses same-field two-replica
interleavings).

## 4. Metrics multiproc guardrail

The deterministic in-host multi-worker signal is the metrics-port bind conflict (uvicorn does
not expose worker count to the app; WEB_CONCURRENCY is unreliable). When `MULTIPROCESS_MODE` is
False and the standalone metrics bind fails with EADDRINUSE/SystemExit(1), `_serve_metrics` logs
`logger.error(...)` stating /metrics reflects only one worker's counters and
`PROMETHEUS_MULTIPROC_DIR` must be set — instead of re-raising inside a never-awaited task
(invisible until shutdown). Rejected fail-fast: would crash N-1 workers in deployments that
currently run (under-reporting but alive) — a breaking change disproportionate to an
observability defect. Multi-host semantics (per-replica scrape targets, no VIP scraping) are
documented in spec+context because no code signal exists there. Test-plan deviation: the
guardrail is regression-tested at the classification/logging helper level
(`_is_metrics_bind_conflict`, `_is_benign_metrics_bind_failure`,
`_log_non_multiproc_metrics_bind_conflict`) because `prometheus_client` is not installed in the
test environment, making the lifespan metrics-server branch unreachable in tests.

## 5. Migration

Revision `20260713_040000_add_replica_guardrails`, parented on the committed main head
`20260712_020000_add_api_key_usage_rollups` (re-parented after the usage-rollup revisions
merged to `main`). Sibling branches in this effort add migrations off the same head; that
cross-branch head fork is resolved at merge time (re-parent whichever lands later; this
revision keeps a distinct `20260713_040000` prefix to avoid collisions). Upgrade: add `dashboard_settings.version`
(`server_default '1'` backfills the existing id=1 row on both backends; batch_alter_table for
SQLite) + create `runtime_sentinels`. Full downgrade drops both.

## 6. Hot-path cost

Zero added per-request synchronous DB round-trips: the fingerprint check is startup-only (two
statements once); the settings version rides the existing UPDATE statement; metrics/guardian
guardrails are log-only. No new polls; no proxy-path changes.

## 7. Triage of remaining replica-locality findings

OAuth flow state, websocket drain, and file pins each need their own table/lifecycle rework and
regression surface — folding any would blow the one-concern and line budgets. Request-log flush
is grouped with websocket drain (same lifespan-shutdown path, same deploy-restart regression
scenario). The additional-usage rewrite tear is low-severity and self-healing, so it is
documented as a known limitation with convergence bounds rather than patched here. Follow-ups:
`persist-oauth-flow-state`, `graceful-drain-lifecycle`, `persist-file-account-pins`.
