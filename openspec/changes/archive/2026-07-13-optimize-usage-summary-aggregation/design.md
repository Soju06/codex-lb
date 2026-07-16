## Context

`UsageService.get_usage_summary` loaded `list_since(now - secondary_window)` — every column of every row — then `_usage_metrics` / `_cost_summary_from_logs` reduced them in Python. `build_usage_summary_response` already accepted `metrics_override` / `cost_override`, so the change threads SQL-aggregated values through the existing override seam and leaves the legacy helpers in place as the equivalence oracle.

## Goals / Non-Goals

**Goals:** O(1)-rows transferred for the summary regardless of window traffic; identical response values.

**Non-Goals:** changing the response schema; touching `list_since`'s other callers; per-model cost exposure (the response only carries the total today).

## Decisions

- **One grouped statement** (`GROUP BY model, is_error, error_code` — models x error codes stays tiny) with all metrics derived from it in Python. Originally three statements, but under READ COMMITTED each SELECT sees its own snapshot, so counts, top error, and cost could describe different committed row sets while the legacy single `list_since` SELECT was internally consistent (Codex review finding). One statement restores one snapshot and still hits `idx_logs_requested_at`. Bonus parity: empty-string error codes are now skipped for top-error exactly like the legacy Python helper.
- **Per-row cached clamp in SQL** via CASE with dialect least/greatest (SQLite's two-argument `min()`/`max()` are its least/greatest).
- **Top-error tie-break becomes deterministic** (count desc, code asc — the dashboard-overview rule) instead of Python dict insertion order; disclosed intentional nuance.
- **Dead refresh removal**: every RequestLog column is assigned before insert and sessions run `expire_on_commit=False`, so the post-commit `refresh` was one guaranteed extra round trip per proxied request.

## Risks / Trade-offs

- [SQL/Python drift over time] → the legacy helpers stay, and the equivalence test seeds every semantic edge the helpers implement.

## Migration Plan

Code-only; rollback = revert.

## Open Questions

None.
