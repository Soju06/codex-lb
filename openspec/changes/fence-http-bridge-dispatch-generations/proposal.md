## Why

Session ownership can advance while an admitted HTTP-bridge predecessor is still finishing. Recovery budget counters can be refunded and do not identify ordinary redispatches. Operation persistence needs its own durable dispatch fence.

## What Changes

- Add a nullable dispatch generation to existing operation rows; new operations explicitly start at zero and each dispatch claims a monotonically increasing generation.
- Fence operation binding, event persistence, terminal settlement, finalization, reset and cleanup by the captured generation, independently of session ownership and recovery budget.
- Fail closed when a legacy row has no dispatch authority, while leaving existing read behavior unchanged.
- Allow a detached predecessor to settle its own operation where legal without publishing successor continuity or aliases.
- Use an additive migration with no backfill or destructive schema change.

## Impact

HTTP-bridge persistence, dispatch and tests only. Direct WebSocket, compact and realtime paths are unchanged.
