# Change: automation-run-pinned-budget

## Why

An automation run claim is reclaimable once it has been held longer than the compact request budget plus a 30 s grace. Until now the window was computed from the budget in effect *at reclaim time*. Since `dashboard-managed-upstream-timeouts` (#2221) made `compact_request_budget_seconds` a live dashboard setting, an operator lowering the budget while a run is in flight shrinks the window under that run: the scheduler (or a second replica) judges the claim stale, re-claims it and starts a second compact ping for the same slot while the first is still running. This was deferred from the #2221 review (codex R2 P2).

## What Changes

- `automation_runs` gains a nullable `claim_budget_seconds` column. Every claim — a fresh slot claim, the runs created by run-now, and a stale reclaim — stores the compact request budget in effect at that moment.
- The stale-claim window of a run covers the larger of its stored budget and the current effective budget (`max(30, max(stored, current) + 30)` seconds), on every path that judges staleness: due-cycle discovery, the scheduled-cycle reclaim, due manual-run discovery and the manual reclaim. Rows claimed before the column existed (NULL) use the current effective budget alone.
- The compact request timeout the run executes with is bounded by the same stored budget, so the execution timeout can never exceed the reclaim window for one run.
- Lowering the dashboard budget therefore applies to claims made after the change only; an in-flight run keeps the window it was claimed under. Raising the budget widens in-flight windows as before, which also keeps a replica that advances `started_at` without refreshing the pin (pre-upgrade code during a rolling deploy) covered by the window while it executes under the current budget.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `automations`: the multi-replica safety requirement derives the stale-claim reclaim window from the larger of the budget captured at claim time and the current budget, and gains scenarios for a lowered dashboard budget, a raised budget over a lower pin, and legacy rows.

## Impact

- Migration `20260909_070000_automation_run_claim_budget` (one nullable column, sqlite and PostgreSQL; downgrade drops it).
- Code: `app/db/models.py`, `app/modules/automations/repository.py`, `app/modules/automations/service.py`.
- API: none (the column is not exposed).
- Operators: no action; existing running rows fall back to the current budget until they complete or are reclaimed.
