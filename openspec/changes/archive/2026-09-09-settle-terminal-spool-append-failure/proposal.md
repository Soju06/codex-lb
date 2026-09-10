## Why

A durable HTTP-bridge operation can remain `acknowledged` after its terminal event has already been delivered downstream when terminal transcript persistence raises. Reconnect and recovery must observe an authoritative terminal outcome rather than treating that operation as incomplete work.

## What Changes

- Settle the operation to its intended terminal state when atomic terminal-event append raises.
- Preserve the existing owner/session/epoch fence, reject settlement after a newer same-owner retry, and leave the event spool explicitly incomplete.
- Keep successful terminal append behavior unchanged.
- Add deterministic unit and production-repository recovery coverage for the failure path.

## Capabilities

### Modified Capabilities

- `responses-api-compat`: terminal HTTP-bridge operation settlement remains authoritative when terminal transcript persistence fails.

## Impact

The HTTP-bridge event batcher, focused durable bridge tests, and reconnect/recovery semantics are affected. Public request and response shapes, graceful-shutdown draining, and warmup behavior are unchanged.
