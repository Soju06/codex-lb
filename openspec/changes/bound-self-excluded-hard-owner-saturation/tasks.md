## 1. Selector evidence

- [x] 1.1 Add `_hard_affinity_owner_excluded_by_caller` to
      `_load_balancer/sticky_selection.py`
- [x] 1.2 Carry it on `StickySelectionOutcome` and report it from
      `run_sticky_selection_path`
- [x] 1.3 Surface it as `AccountSelection.hard_affinity_owner_excluded` from
      `LoadBalancer.select_account`

## 2. Recovery wait

- [x] 2.1 `_account_selection_recovery_sleep_seconds` returns `None` for a
      self-excluded hard owner, so every transport fails closed on the first
      saturated re-selection

## 3. Spec + tests

- [x] 3.1 ADDED `sticky-session-operations` requirement + scenarios
- [x] 3.2 Selection-level: raw legacy row + owner excluded reports the flag;
      unexcluded control resolves the owner; owner unavailable without an
      exclusion keeps its recovery wait
- [x] 3.3 Support-level: the wait is refused without sleeping once
- [x] 3.4 Reconnect-level bound: one selection attempt, no sleep, fail closed;
      control still waits once and retries
- [x] 3.5 Relay-level: created-only and model-fallback replays on a raw legacy
      hard owner fail closed within one re-selection (both shapes spun 294/295
      attempts to the budget before this change)
- [x] 3.6 Mutants: drop the error-code condition, drop the exclusion-set
      condition, drop the support guard, and stub the load-balancer plumbing —
      each caught by a listed test
