## Why

Stale HTTP bridge retirement currently decides from a pre-await zero-event snapshot. A healthy pending turn can receive its first response event while retry-circuit bookkeeping suspends, then be closed and removed anyway.

## What Changes

- Re-sample pending-turn liveness immediately before stale retirement closes a session.
- Perform the registry identity, pending-state liveness, and close decision under the existing bridge/session locks.
- Preserve retirement for sessions that remain genuinely eventless.
- Add regression and control coverage for both outcomes.

## Capabilities

### New Capabilities

### Modified Capabilities

- `responses-api-compat`: stale HTTP bridge retirement must not kill a turn that became healthy during retry-circuit suspension.

## Impact

The change is limited to `request_submit.py`, HTTP bridge unit coverage, and the Responses API compatibility OpenSpec delta. No public endpoint or schema changes are introduced.
