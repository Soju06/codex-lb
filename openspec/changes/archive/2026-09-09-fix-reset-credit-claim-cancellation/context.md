# Cancellation-safe reset-credit claim release

## Purpose

Lease expiry is crash recovery, not the normal release mechanism for a live
process that receives request cancellation.

## Decision

Heartbeat cancellation/drain and the existing holder-fenced release form one
owned cleanup coroutine. The canonical defer-cancellation helper runs that
coroutine to completion and then surfaces the captured cancellation.

Cleanup order remains heartbeat cancel, heartbeat drain, claim release, then
deferred cancellation propagation.

## Constraints

- Do not shield or extend the redemption body.
- Do not alter acquisition, holder fencing, lease duration, renewal, or timeout.
- Do not change PostgreSQL or session-less locking.
- Preserve the existing logged lease-expiry fallback for genuine DB errors.

## Example

Cancellation enters cleanup, then cancellation is delivered again while the
SQLite delete is suspended. Release commits first, cancellation propagates
second, and a successor acquires immediately instead of waiting 30 seconds.
