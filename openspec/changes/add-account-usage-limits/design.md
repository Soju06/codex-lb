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

When historical monthly telemetry and a normalized weekly-only shape coexist, they are alternative observations of the account's long-window quota rather than independent windows. The evaluator chooses between them with the shared sibling-fetch rules: timestamps more than the sibling margin apart establish fetch order; rows within the margin use quota metadata and reset deadlines; an exact tie keeps the stable weekly-primary default. A genuinely newer weekly observation can therefore replace an elapsed or stale monthly row after an upstream shape/reset transition, while a newer or same-fetch authoritative monthly observation remains canonical.

Rows whose reset deadline elapsed are not exhaustion evidence. The remaining relevant rows must be fresh according to the existing usage-refresh freshness window; if there is no fresh relevant standard row, the limit state is `data_unavailable` and selection fails closed. This favors preserving quota over availability while a hard operator policy is active.

The reusable evaluator returns one of `disabled`, `available`, `reached`, or `data_unavailable`. Account summaries and proxy state use the same evaluator so the dashboard cannot claim a different cap state from the selector.

### D3: Standard usage remains available during additional-quota routing

Selection inputs retain cloned standard primary, secondary, and monthly rows separately from request-priority rows. Gated models may still rank and validate against their additional quota, but the hard account limit is always evaluated from standard rows and is never bypassed by `ignore_standard_quota`.

### D4: The canonical selector owns the hard gate

`AccountState` carries the evaluated limit state and percentage. `select_account` applies the policy after status, upstream-quota, and cooldown checks, but before error-backoff classification, health, stickiness, policy, or strategy handling. This makes the local policy authoritative only for accounts that would otherwise be routing candidates: an account that is also upstream-exhausted retains the established `usage_limit_reached` 429 and reset metadata, while a locally blocked account never enters the error-backoff fallback set. If all otherwise eligible candidates are blocked by limits, selection returns stable error code `account_usage_limit_reached`; opportunistic prechecks preserve that typed error instead of rewriting it. The account's persisted `active`/rate-limit status is not changed.

Fair-share admission derives capacity and lease/key counters from the same usage-policy-eligible candidate set used for routing. Locally blocked accounts contribute neither capacity nor in-flight counters; an entirely locally blocked pool bypasses fair-share admission and reaches the canonical policy error.

Reused HTTP bridges are continuity owners, not fresh selection opportunities. Turn admission therefore extracts the pinned account and standard rows from one canonical global selection snapshot and calls the evaluator directly before accepting a retained lease or reacquiring a released one. Cached probes share that immutable snapshot and its account-id index without cloning fleet-sized lists or maps. The read-only probe neither builds runtime states nor creates per-owner cache entries. It never asks the selector for an alternate owner. A denial marks the session to retire after drain so already-admitted turns keep their ownership and settlement paths; a missing or administratively unavailable owner uses the established continuity-lost response instead of a local usage-limit error.

Quota warmup planning consumes the usage-limit state already evaluated on `AccountState`. Because a planned action can outlive that snapshot, execution freshly reads the account plus its standard primary, secondary, and monthly rows after the decision claim and optional API-key reservation, then requires the account to remain active and runs the same evaluator immediately before the probe send. A denial releases the reservation and changes the claimed decision from `executing` to `skipped`. Disabled and available policies remain neutral.

### D5: Dashboard writes invalidate selection state across replicas

`PUT /api/accounts/{account_id}/usage-limit` accepts the enabled flag and nullable percentage, returns the persisted pair, invalidates the local selection-input cache, and emits the existing account-selection invalidation signal for peers. Account summaries expose the fields and evaluated state. The Accounts page provides percentage editing, enable/disable, and remove actions with wording that makes `10%` mean “10% maximum used / 90% reserved.” The dashboard initializes the editable value from the persisted number without decimal-place quantization, so every API-valid percentage remains valid and unchanged until the operator edits it.

### D6: The guarantee is observation-bound

Codex LB cannot know the upstream percentage cost of a request before sending it. The hard contract is therefore: once the latest current standard usage observation is at or above the cap, Codex LB never selects that account until the window resets, the telemetry becomes current below the cap, or the operator disables/removes the cap. The UI explains that delayed upstream reporting and in-flight requests can overshoot the displayed percentage.

## Migration

A forward Alembic revision based on the current upstream migration head adds both account columns and database checks for the percentage range and enabled/value relationship. Existing accounts remain disabled with no percentage. Downgrade removes the checks and columns through batch operations so SQLite and PostgreSQL both round-trip.

## Test plan

- Pure evaluator tests for disabled, available, reached, stale, missing, elapsed, weekly-only, and monthly-only windows.
- Selector tests proving equality blocks, one limited account falls back to another, all-limited returns the stable error, locally blocked accounts cannot enter backoff fallback, and standard limits survive the additional-quota bypass flag.
- Load-balancer tests proving standard rows gate a request whose ranking rows come from an additional quota and sticky selection cannot reuse a capped account.
- Accounts API/service/mapper tests for set, disable-retain, remove, validation, response state, and cache invalidation.
- Migration upgrade/downgrade/upgrade coverage.
- Dashboard schema, request hook, control interaction, and reached-state presentation tests.
- Reused HTTP bridge admission tests for retained and released leases, including public policy errors and drain-safe retirement.
- Quota planner and execution-gate tests for reached, unavailable, available, and disabled usage-limit states.
