## Why

After an upstream 429, a fresh usage sample can clear the observing replica's account block once its short cooldown expires even when the blocked window still reports 100% usage. This makes the existing HTTP usage-refresh regression depend on timing and sends another upstream request instead of returning structured pool exhaustion.

## What Changes

- Require available usage evidence before the existing early-recovery path clears a real upstream block.
- Preserve ordinary active-account eligibility at advisory usage exhaustion, elapsed-window normalization, credit handling, and peer-replica cooldown enforcement.
- Extend existing deterministic recovery coverage and verify the existing HTTP refresh/cache regression without changing its response assertions.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `account-routing`: Clarify that the observing replica's early recovery requires the blocked window to show available quota.

## Impact

The change is limited to the existing usage-recovery condition in `app/modules/proxy/load_balancer.py`, its existing tests, and the owning specification. It adds no setting, schema, endpoint, or dependency.
