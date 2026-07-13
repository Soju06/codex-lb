# Document replica topology contract

## Why

codex-lb ships first-class multi-replica machinery (bridge ring, owner forwarding, leader election, cache-invalidation bus), but no spec in the SoT defines what a supported multi-replica deployment requires — shared PostgreSQL, a shared encryption key, leader election opt-in, metrics multiproc semantics — while usage-refresh-policy normatively cites a leader-election mechanism no spec specifies. Verified defects cluster around this gap: replicas never verify they share the same encryption key (divergent keys fail replica-dependently at use time), `PUT /api/settings` silently loses concurrent updates including security toggles, and a multi-worker `/metrics` endpoint silently reports 1/N of traffic. This change authors the replica-operations topology contract as a new capability, lands the three small guardrails that belong with it, and triages the remaining replica-locality findings into named follow-ups or documented known limitations.

## What Changes

- **NEW capability `replica-operations`** (`spec.md` + `context.md`): supported multi-replica topology — shared PostgreSQL required for >1 replica; SQLite is single-process; leader election expectations and default-disabled consequences; bridge ring / instance-id / advertise-URL prerequisites; encryption-key consistency requirement; metrics semantics with and without `PROMETHEUS_MULTIPROC_DIR`. Context carries rationale, ops runbook, and known limitations for triaged findings.
- **Guardrail 1 — startup encryption-key fingerprint check:** new `runtime_sentinels` table; each replica stamps/verifies `sha256(key)` via atomic insert-if-absent; mismatch refuses startup (`enforce` default, env-overridable to `warn`/`off`) with a remediation message.
- **Guardrail 2 — `PUT /api/settings` optimistic locking:** `dashboard_settings.version` with SQLAlchemy `version_id_col`; the concurrent-writer loser gets 409 `settings_conflict`; responses expose `version`; payload accepts optional `expectedVersion`; internal single-field writers retry once on conflict.
- **Guardrail 3 — metrics multiproc detection:** a non-multiproc metrics-port bind conflict now logs a loud ERROR ("/metrics reflects only one worker; set PROMETHEUS_MULTIPROC_DIR") instead of raising inside an unobserved task.
- **Fold-ins:** startup WARNING when the auth guardian self-disables (multi-replica ring without leader election) + MODIFIED usage-refresh-policy requirement documenting that contract; rewrite stale responses-api-compat fail-closed claims to describe owner forwarding.
- One Alembic migration (`dashboard_settings.version` + `runtime_sentinels`) with downgrade, both dialects.

## Non-goals

- Dashboard frontend adoption of `expectedVersion` (follow-up).
- Replica-locality fixes triaged into follow-up changes: `persist-oauth-flow-state`, `graceful-drain-lifecycle`, `persist-file-account-pins` (see design.md and replica-operations context known limitations).
