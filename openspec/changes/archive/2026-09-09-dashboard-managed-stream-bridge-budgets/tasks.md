# Tasks

## 1. Backend

- [x] 1.1 Alembic `20260909_080000_dashboard_stream_bridge_budgets`: two nullable `FLOAT` columns on `dashboard_settings` (SQLite batch + PostgreSQL), downgrade drops them; single alembic head.
- [x] 1.2 ORM columns, `DashboardSettingsData` effective fields, `DashboardSettingsUpdateData` tri-state fields, repository `update()` value/clear pairs; `DASHBOARD_TIMEOUT_SETTINGS` extended so provenance, the PUT-time invariant check and the audit loop cover both.
- [x] 1.3 `DashboardSettingsResponse` / `DashboardSettingsUpdateRequest` fields; `upstream-connect-within-stream-budget` and `upstream-connect-within-bridge-budget` rules; stale source anchors in `timeout_invariants.py` refreshed (`STREAM_BUDGET`, `BRIDGE_BUDGET`, `ADMISSION_WAIT`, stuck gate).
- [x] 1.4 Background consumers: quota warm-up claim lease floor resolved from the scheduler's per-tick dashboard snapshot; bridge stale-operation abandonment sweep reads one snapshot per heartbeat pass (environment fallback on snapshot failure).
- [x] 1.5 `MIGRATING` entries removed; `# T3 → dashboard (deprecated env alias, remove next minor)` on both `Settings` fields; `20260830` warmup-claim migration comment reworded as a default mirror.
- [x] 1.6 Tests: resolver three states; API round-trip default → dashboard → env → null-clear; invariant rejections (connect > stream budget, bridge budget <= 600, stream budget < admission wait, schema bounds); consumers — stream deadline honours a dashboard 3600 s over env 7200 s inside a bound snapshot, warm-up claim lease honours a dashboard 3000 s, abandonment cutoff honours a dashboard 10800 s and falls back on snapshot failure; migration upgrade/downgrade/head walk.

## 2. Dashboard

- [x] 2.1 Response/update zod fields (optional with code defaults; tri-state nullable on update).
- [x] 2.2 Two rows in the Upstream timeouts card with `InheritBadge`, placeholder, connect-within-stream/bridge-budget, bridge-budget-above-600 and stream-budget-admission-wait mirrors, plus both fields in the card's remount key; tests.
- [x] 2.3 `settings.upstreamTimeouts.{streamBudget,bridgeBudget}.*` and `bridgeBudgetTooLow` in `en`, `ko`, `zh-CN` (key-parity test).
- [x] 2.4 Before/after screenshots (light and dark) in the PR.

## 3. Verification

- [x] 3.1 `make lint` (incl. `check_settings_tiers`), `uv run ty check`, `make migration-check`, unit + integration tests, frontend lint/typecheck/vitest, `openspec validate --specs --strict`, `openspec validate dashboard-managed-stream-bridge-budgets --strict`, archive simulation on a copy of main's `openspec/`, `docs/reference/settings.md` regenerated.
