## 1. Window carrier

- [x] 1.1 Add `primary_window_minutes` to `AccountState` and populate it from `_state_from_account` (cleared alongside the weekly-primary remap and zero-capacity handling).

## 2. Planner scoping and duration math

- [x] 2.1 Derive active/cold window state from `AccountState.primary_reset_at` instead of blocked-status `reset_at`.
- [x] 2.2 Gate warmup candidacy and cold-start routing costs on short-window plannability (duration metadata present and <= 24h).
- [x] 2.3 Thread per-account window duration through candidate start times, scoring, planned resets, and pool simulation; add `window_seconds` to `PlannerAction`.

## 3. Warm-up gates

- [x] 3.1 Execution gate refuses accounts whose latest primary-slot sample positively reports a long (weekly/monthly) window (`no_short_window`); absent or metadata-less samples keep legacy bootstrap behavior.
- [x] 3.2 Warmup-effect observations do not reach observed confidence from a long-window primary-slot sample.
- [x] 3.3 Staggered idle limit warm-up derives its rolling cycle from the observed primary window duration (300-minute fallback for missing metadata).

## 4. Validation

- [x] 4.1 Unit coverage: weekly-only accounts get no warmups/cold costs; mixed pools plan only short-window accounts; healthy active windows are not cold; per-account duration math; idle warm-up stagger uses observed duration.
- [x] 4.2 Run targeted planner, limit-warmup, and load-balancer suites.
- [x] 4.3 Validate the OpenSpec change with `openspec validate planner-window-generalization --strict`.
