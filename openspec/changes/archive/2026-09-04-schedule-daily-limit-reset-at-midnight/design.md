## Context

API-key limits persist a naive-UTC `reset_at`. Fresh reset timestamps are currently derived by adding a fixed duration to the current instant, and the existing scheduler runs an immediate/hourly leader-gated pass for expired limits and stale reservations. See [proposal.md](proposal.md) for motivation and [specs/api-keys/spec.md](specs/api-keys/spec.md) for the changed contract.

## Goals / Non-Goals

**Goals:**

- Calculate all fresh daily boundaries as the next UTC midnight.
- Normalize legacy/non-aligned daily rows ten minutes before that boundary without clearing usage.
- Reuse the API-key scheduler's lifecycle and shared leader gate.
- Keep timing and boundary logic deterministic under unit tests.

**Non-Goals:**

- Introduce an operator timezone or a new `CODEX_LB_*` setting.
- Change the fixed-duration semantics of `5h`, `7d`, weekly, or monthly limits.
- Replace lazy expiry handling or make the hourly fallback fire at an exact wall-clock minute.

## Decisions

### Treat UTC midnight as the single daily boundary

`next_limit_reset` will return the following date at `00:00:00` for `daily`; the existing `advance_limit_reset` then preserves that alignment by adding whole days. UTC matches the persisted timestamp convention and avoids DST-dependent window lengths.

Alternative considered: server-local midnight. Rejected because the API-key limit model has no timezone identity and a host timezone change would silently move a persisted quota boundary.

### Add a wall-clock loop to the existing scheduler

The API-key reset scheduler will own a second cancellable task. It computes the delay to the next 23:50 UTC, waits through the scheduler stop event, then enters the same shared leader-election gate used by the hourly fallback. Keeping the loops separate prevents process start time from shifting the wall-clock job and leaves the existing hourly cadence unchanged.

Alternative considered: check for 23:50 inside the hourly loop. Rejected because an hourly interval anchored to process startup cannot guarantee that minute and can skip the daily pass entirely.

### Normalize with one conditional repository update

The repository will update only `daily` rows whose `reset_at` differs from the supplied next-midnight target, set only `reset_at`, return the affected identifiers for a backend-portable count, and commit once. `current_value` remains untouched. Existing reservation compare-and-set semantics remain authoritative: an in-flight reservation retains its already-booked reserved amount if its old reset epoch no longer matches, and settlement cannot write an old epoch into a new window.

Alternative considered: clear the usage counter at 23:50. Rejected because that grants quota ten minutes before the declared daily boundary.

## Risks / Trade-offs

- [Risk] Deployment after 23:50 leaves legacy rows non-aligned until the following day's pass. → Fresh or explicitly reset daily rules are aligned immediately; lazy/hourly expiry behavior remains safe during the transition.
- [Risk] A process restart or leader lease failure exactly at 23:50 misses that day's pass. → The next day's pass is idempotent, and request-path expiry still enforces the stored boundary.
- [Risk] An in-flight reservation spans the one-time legacy alignment. → The reset-epoch compare-and-set prevents cross-window settlement; its conservative reserved amount remains booked for at most the ten minutes before midnight.

## Migration Plan

1. Deploy the code with the new boundary calculation and scheduler loop.
2. At the next 23:50 UTC leader tick, normalize existing daily rows in place.
3. Verify scheduler logs and representative daily `reset_at` values at 00:00 UTC.

Rollback is a code-only revert. Already-normalized rows remain valid fixed-duration daily windows anchored at midnight, so no data rollback is needed.
