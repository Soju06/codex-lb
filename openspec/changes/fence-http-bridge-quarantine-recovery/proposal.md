# Fence HTTP bridge quarantine recovery

## Why

HTTP bridge quarantine is intentionally bounded and session-scoped. PR #1862 restores a recovery path that does more than clear the in-memory quarantine entry: when a replacement request completes and produces a usable response id, it also advances the original durable session row so later continuations can reuse the recovered anchor.

That durable write is useful, but the current contract still says quarantine clearing never writes the durable row. The implementation also needs sharper fences: a stale quarantine generation must not advance durable continuity after the same key was quarantined again, and a fenced `renew_live_session` call must not be treated as success unless the returned snapshot proves the intended row was updated.

## What Changes

- Update the `responses-api-compat` quarantine contract so a completed recovery response may rebind and renew the original durable session row.
- Require that durable recovery is fenced by the captured quarantine generation before durable mutation.
- Require that the renewal snapshot matches the expected session id, owner instance, owner epoch, account id, and recovered response id before clearing quarantine.
- Keep the existing boundaries: no account-health write, no account-selection change, TTL cleanup stays in-memory, and a stale generation leaves the newer quarantine and durable owner/anchor intact.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `responses-api-compat`: HTTP bridge quarantine recovery may perform a fenced durable ownership transfer when a replacement response completes, and must prove the durable renewal before clearing the quarantine entry.
