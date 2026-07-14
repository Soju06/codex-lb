# Design: add-account-usage-limits

## Context

`Account` stores operator-controlled routing fields, while `AccountState` is the canonical selector input used by ordinary, sticky, single-account, opportunistic, bridge, file, and gated-model routes. Standard quota rows are loaded alongside additional-quota rows, but gated-model selection can replace the priority rows used for ranking. A standard account limit therefore cannot be implemented as another soft budget filter or by reading only `AccountState.used_percent`.

## Goals

- Let an operator cap one account at a maximum observed used percentage such as 10%.
- Make the cap reversible without losing the configured percentage.
- Ensure every selector and fallback respects the cap.
- Keep standard account-limit state separate from persisted upstream account status.
- Degrade toward preserving quota when usage telemetry is unavailable.

## Decisions

### D1: Persist configuration and activation separately

Accounts gain nullable `usage_limit_percent` and non-null `usage_limit_enabled` fields. A disabled row retains its percentage for one-click re-enablement; removing the limit clears the percentage and disables it. Enabled-without-percentage is invalid. Percentages are greater than 0 and at most 100.

### D2: One cap applies to every current standard quota window

The evaluator normalizes weekly-only and monthly-only account shapes using the same window rules as routing and account presentation. An enabled account is blocked when any current standard primary, weekly, or monthly window reports `used_percent >= usage_limit_percent`.

Rows whose reset deadline elapsed are not exhaustion evidence. The remaining relevant rows must be fresh according to the existing usage-refresh freshness window; if there is no fresh relevant standard row, the limit state is `data_unavailable` and selection fails closed. This favors preserving quota over availability while a hard operator policy is active.

The reusable evaluator returns one of `disabled`, `available`, `reached`, or `data_unavailable`. Account summaries and proxy state use the same evaluator so the dashboard cannot claim a different cap state from the selector.

### D3: Standard usage remains available during additional-quota routing

Selection inputs retain cloned standard primary, secondary, and monthly rows separately from request-priority rows. Gated models may still rank and validate against their additional quota, but the hard account limit is always evaluated from standard rows and is never bypassed by `ignore_standard_quota`.

### D4: The canonical selector owns the hard gate

`AccountState` carries the evaluated limit state and percentage. `select_account` removes `reached` and `data_unavailable` states before cooldown, health, stickiness, policy, or strategy handling. Backoff fallback cannot reintroduce them. If all candidates are blocked by limits, selection returns stable error code `account_usage_limit_reached`; the account's persisted `active`/rate-limit status is not changed.

### D5: Dashboard writes invalidate selection state across replicas

`PUT /api/accounts/{account_id}/usage-limit` accepts the enabled flag and nullable percentage, returns the persisted pair, invalidates the local selection-input cache, and emits the existing account-selection invalidation signal for peers. Account summaries expose the fields and evaluated state. The Accounts page provides percentage editing, enable/disable, and remove actions with wording that makes `10%` mean “10% maximum used / 90% reserved.”

### D6: The guarantee is observation-bound

Codex LB cannot know the upstream percentage cost of a request before sending it. The hard contract is therefore: once the latest current standard usage observation is at or above the cap, Codex LB never selects that account until the window resets, the telemetry becomes current below the cap, or the operator disables/removes the cap. The UI explains that delayed upstream reporting and in-flight requests can overshoot the displayed percentage.

## Migration

A forward Alembic revision based on the current upstream migration head adds both account columns and database checks for the percentage range and enabled/value relationship. Existing accounts remain disabled with no percentage. Downgrade removes the checks and columns through batch operations so SQLite and PostgreSQL both round-trip.

## Test plan

- Pure evaluator tests for disabled, available, reached, stale, missing, elapsed, weekly-only, and monthly-only windows.
- Selector tests proving equality blocks, one limited account falls back to another, all-limited returns the stable error, and standard limits survive the additional-quota bypass flag.
- Load-balancer tests proving standard rows gate a request whose ranking rows come from an additional quota and sticky selection cannot reuse a capped account.
- Accounts API/service/mapper tests for set, disable-retain, remove, validation, response state, and cache invalidation.
- Migration upgrade/downgrade/upgrade coverage.
- Dashboard schema, request hook, control interaction, and reached-state presentation tests.
