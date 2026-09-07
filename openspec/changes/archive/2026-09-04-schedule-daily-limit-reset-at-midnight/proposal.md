## Why

Daily API-key limits currently inherit their reset clock from the instant the rule is created, so different keys reset at different times of day. Operators need daily windows to converge on a predictable calendar boundary while preserving the existing lazy and background reset safety nets.

## What Changes

- Define daily API-key reset boundaries as 00:00 UTC.
- Run a leader-gated alignment pass at 23:50 UTC every day so existing daily limit rows point to the next midnight without clearing their current usage early.
- Keep non-daily limit windows and the existing expired-limit fallback behavior unchanged.
- Add deterministic unit and repository/integration coverage for boundary calculation, scheduling, row selection, and usage preservation.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `api-keys`: Daily API-key limit reset timestamps become midnight-aligned and existing rows are normalized by a daily scheduled pass.

## Impact

- `app/modules/api_keys/limit_windows.py`: calculate daily reset boundaries at the next UTC midnight.
- `app/modules/api_keys/reset_scheduler.py`: schedule the daily 23:50 UTC alignment pass alongside the existing fallback sweep.
- `app/modules/api_keys/repository.py`: normalize daily reset timestamps without resetting counters.
- API-key scheduler, limit-window, repository, and integration tests.
- No schema migration, new setting, public API change, or dependency.
