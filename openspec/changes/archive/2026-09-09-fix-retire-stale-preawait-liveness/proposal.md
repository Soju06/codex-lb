## Why

Stale HTTP bridge retirement currently decides from a pre-await zero-event snapshot. A healthy pending turn can receive its first response event while retry-circuit bookkeeping suspends, then be closed and removed anyway.

## What Changes

- Re-sample pending-turn liveness immediately before stale retirement closes a session.
- Perform the registry identity, pending-state liveness, and close decision under the existing bridge/session locks.
- Preserve retirement for sessions that remain genuinely eventless.
- Exempt reader-failure and already-closed-session retirement from the revive: their turns are already terminally failed, and durable-anchor rehydration can move the completed-response anchor without upstream evidence.
- Add regression and control coverage for both outcomes.

## Capabilities

### New Capabilities

### Modified Capabilities

- `responses-api-compat`: stale HTTP bridge retirement must not kill a turn that became healthy during retry-circuit suspension.

## Impact

The change is limited to `support.py` (upstream event-generation counter), `http_bridge/upstream_events.py` (counter increment, reader-failure retirement revive opt-out), `http_bridge/request_submit.py` (retirement liveness recheck), HTTP bridge unit coverage, and the Responses API compatibility OpenSpec delta. No public endpoint or schema changes are introduced.
