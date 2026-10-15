## Why

An immediate anchored HTTP follow-up can arrive after the preceding terminal event and EOF while detached request-log persistence is still pending. The real-route experiment reproduced 35 failures in96 immediate attempts; publishing through the existing process cache eliminated those failures.

## What Changes

- Publish authoritative upstream Responses IDs to the existing bounded owner cache before exposing them to the HTTP client.
- Preserve account/API-key/session scoping, durable lookup and unknown-owner fail-closed behavior.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `responses-api-compat`: Immediate same-process HTTP continuation ownership readiness at the first observable upstream response-ID boundary.

## Impact

The HTTP streaming attempt and existing owner-cache method, plus actual Responses-route regressions. No new registry/schema or cross-replica readiness promise; request logs remain detached.

Partial investigation follow-up for issue #2029; this scope does not independently close the broad performance issue.
