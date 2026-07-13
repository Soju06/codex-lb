# Tasks

## 1. Specs and docs

- [x] 1.1 Scaffold change folder with proposal, design, tasks, and delta specs (replica-operations ADDED + usage-refresh-policy MODIFIED).
- [x] 1.2 Author `openspec/specs/replica-operations/spec.md` (five ADDED requirements) and `context.md` (topology overview, ops runbook, known limitations with follow-up change names).
- [x] 1.3 Rewrite stale responses-api-compat `context.md` / `ops.md` fail-closed claims to describe owner forwarding as the primary hard-continuity path.

## 2. Migration and models

- [x] 2.1 Migration `20260713_040000_add_replica_guardrails` on committed head `20260712_020000_add_api_key_usage_rollups`: add `dashboard_settings.version` (server_default '1', batch_alter_table for SQLite) + create `runtime_sentinels`; full downgrade.
- [x] 2.2 Models: `DashboardSettings.version` with `version_id_col`; new `RuntimeSentinel`.

## 3. Guardrails

- [x] 3.1 Encryption fingerprint check (`app/core/config/key_fingerprint.py`, dialect INSERT..ON CONFLICT DO NOTHING stamp, enforce/warn/off via `encryption_key_fingerprint_mode`); wire into lifespan after `init_db()` and before `ensure_auto_bootstrap_token()`.
- [x] 3.2 Settings optimistic locking: `DashboardSettingsConflictError`; `SettingsRepository.commit_refresh` maps `StaleDataError` → 409 `settings_conflict`; API honors optional `expectedVersion` and binds the handler-read version into the repository update (`expected_version` CAS on the versioned row) so writers committing between the check and the UPDATE still lose with 409; response exposes `version`; retry-once wrapper for dashboard-auth single-field writers.
- [x] 3.3 Metrics guardrail: non-multiproc bind conflicts log an ERROR with the `PROMETHEUS_MULTIPROC_DIR` remediation instead of re-raising into the unobserved task.
- [x] 3.4 Guardian warning: `build_auth_guardian_scheduler` logs a WARNING when the guardian self-disables (multi-replica ring without leader election).

## 4. Verification

- [x] 4.1 Regression tests: two-replica fingerprint stamp/mismatch/warn/concurrent first boot + lifespan startup refusal; concurrent PUT /api/settings 409 at the route; stale `expectedVersion` 409; interleaved commit between the `expectedVersion` check and the service update 409; dashboard-auth writer retry; metrics bind-conflict classification and ERROR log (helper level — `prometheus_client` is absent in the test env, so the lifespan metrics branch is unreachable); guardian warning; migration round-trip with version backfill and single Alembic head.
- [x] 4.2 Run focused pytest, ruff check/format, and `openspec validate document-replica-topology-contract`.
