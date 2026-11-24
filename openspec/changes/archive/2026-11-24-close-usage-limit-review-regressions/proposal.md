## Why

Review of the per-account quota reserve found gaps in sticky admission, owner errors, historical usage reads, and recovery of interrupted migration repairs.

## What Changes

- Preserve the provider reset time and the local reserve denial on their respective owner-selection paths.
- Keep capped accounts out of soft sticky admission and finish cleanup after a rejected recovery probe or expired WebSocket frame.
- Exclude unavailable placeholders from capped history queries before counting rows.
- Resume an interrupted relative upgrade against its original target.

## Capabilities

### Modified Capabilities

- `account-routing` covers owner selection, cleanup, and usage history.
- `database-migrations` covers historical usage-limit ledger repair.

## Impact

Sticky selection, WebSocket admission, usage history reads, and historical migration recovery.
