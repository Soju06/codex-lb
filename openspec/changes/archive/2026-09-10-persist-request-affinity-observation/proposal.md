## Why

Request logs cannot identify the affinity decision used for a request. Issue #2349 asks for durable, content-free metadata so operators can compare resolved keys without reconstructing threads from token counts.

## What Changes

- Persist nullable sticky-key source, kind, and stable hash on existing request-log rows.
- Carry the already resolved decision through Responses and compact success/failure logging without changing routing or settlement.
- Expose these fields through the authenticated request-log listing and retain existing retention and access controls.
- Add a forward migration with historical values left null.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `proxy-runtime-observability`: Durable affinity metadata on request-log rows.

## Impact

Request-log schema, repository, API mapping, and existing proxy log emitters. No frontend, configuration, account-health, routing-policy, or live-runtime changes.
