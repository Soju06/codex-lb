# Tasks

## 1. Backend

- [x] 1.1 Alembic revision adding nullable `automation_runs.claim_budget_seconds`; ORM column and `AutomationRunRecord` field.
- [x] 1.2 Every claim path (`claim_run`, `claim_scheduled_cycle_account_run`, `create_run_cycle_with_runs`, `claim_manual_run_execution`, `claim_scheduled_cycle_run_execution`) stores the effective compact budget on the row.
- [x] 1.3 Staleness is judged per run from the stored budget (current budget for NULL) in due-cycle discovery, due manual-run discovery and both reclaim paths; the compact request timeout uses the same stored budget.

## 2. Verification

- [x] 2.1 Integration: claims and reclaims store the budget in effect; a claim pinned at 600 s is not reclaimed after the dashboard lowers the budget to 60 s and is reclaimed past its own window; a legacy NULL row follows the current budget; `list_due_manual_runs` honours the pinned window and its `limit` counts only eligible rows.
- [x] 2.2 Unit: window helper, per-run staleness predicate and the pinned compact timeout.
- [x] 2.3 Migration upgrade/downgrade test; `make lint`, `uv run ty check`, `make migration-check`, `openspec validate automation-run-pinned-budget --strict`.
