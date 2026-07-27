## Why

An account that enters the replica-local `PROBING` health tier can remain unused forever whenever another healthy account exists, because selection always excludes probing accounts from the effective pool in that case. The same recovery state is invisible to the dashboard Force Probe path, so even a successful operator probe cannot rehabilitate the account for routing.

## What Changes

- Admit at most one due probing account ahead of healthy accounts after the existing fixed probe quiet interval, using replica-local selection timestamps to keep recovery traffic bounded and fair.
- Preserve existing sticky ownership: recovery admission applies only when normal selection is choosing a new or fallback account, not when a selectable sticky owner is retained.
- Feed successful dashboard Force Probe results into the same replica-local probe-success state; non-success responses do not count as recovery successes.
- Keep the behavior zero-config by reusing the existing fixed quiet interval and success-streak constants.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `account-routing`: Require bounded, starvation-free recovery admission for replica-local probing accounts.
- `usage-refresh-policy`: Require operator Force Probe outcomes to update effective replica-local probe recovery state without treating non-success responses as successes.

## Impact

- Account selection and runtime health state in `app/core/balancer/logic.py` and `app/modules/proxy/load_balancer.py`.
- Force Probe orchestration across `app/modules/accounts` and the process-local proxy service.
- Focused balancer, account-service/API, and regression tests.
- No database migration, new setting, environment variable, or response-schema change.
