## Context

Rate-limit errors may carry an absolute `resets_at` value, a relative `resets_in_seconds` value, a duration in the message, or no reset hint. The balancer currently converts explicit numeric metadata directly into an integer deadline and persists it. Cross-replica cooldown enforcement and usage-refresh recovery then correctly trust that durable deadline, but they have no way to distinguish a real monthly reset from a malformed value centuries in the future.

The fix crosses the pure balancer logic, selection-time reconstruction of persisted state, and background usage recovery. It must preserve the cooldown guarantees introduced for peer replicas while allowing existing poisoned rows to self-heal without a migration.

## Goals / Non-Goals

**Goals:**

- Accept legitimate finite reset metadata for all currently supported quota windows.
- Prevent malformed, wrong-unit, elapsed, or non-finite metadata from creating an effectively permanent block.
- Reuse the existing Retry-After/backoff fallback when explicit metadata is unusable.
- Repair already-persisted implausible deadlines through ordinary CAS-guarded selection or usage-refresh recovery.

**Non-Goals:**

- Guess the unit or intended value of malformed OpenAI service metadata.
- Add an operator setting, database migration, or bulk cleanup job.
- Change the normal cooldown or recovery rules for plausible deadlines.

## Decisions

### Use one conservative hardcoded plausibility horizon

Add a pure balancer helper that accepts a deadline only when it is finite, strictly later than the evaluation time, and no more than 366 days in the future. Relative durations must likewise be finite, positive, and no greater than 366 days before being converted to an absolute deadline.

Persistence accepts less than one second of additional horizon only for the whole-second ceiling applied to an already-valid deadline. This keeps horizon-edge values stable across write/read reconstruction without widening acceptance of raw OpenAI service metadata.

The supported usage windows currently top out at a month. A one-year horizon leaves substantial compatibility margin without allowing wrong-unit millisecond/nanosecond values to pin an account for decades or centuries. This is a safety invariant rather than an operator preference, so it remains a constant rather than a new setting.

Alternatives considered:

- Derive the limit from a usage-window field. Rejected because rate-limit error metadata does not reliably carry the affected window identity.
- Convert suspicious values as milliseconds or nanoseconds. Rejected because guessing can shorten a legitimate cooldown or interpret unrelated numeric data as a timestamp.
- Clamp suspicious values to the horizon. Rejected because it would still block an account for a year; invalid metadata should use the existing bounded fallback.

### Prefer usable explicit metadata, then preserve the existing fallback chain

Evaluate `resets_at` first. If it is invalid, evaluate `resets_in_seconds`; if neither is usable, behave exactly as if explicit reset metadata were absent and resolve the message duration or error-count backoff. Round an accepted fractional deadline up to the next integer second so persistence cannot shorten it.

### Apply the same validation to persisted rate-limit rows

Selection reconstruction and background usage recovery will validate `accounts.reset_at` before treating it as an unexpired 429 cooldown. An implausible value is treated as missing metadata, so a row with `blocked_at` still honors the existing 30-second minimum floor. After that floor, fresh recovery evidence can clear `status`, `reset_at`, and `blocked_at` through the existing compare-and-set write.

This read-time interpretation repairs existing rows without a migration and remains safe under concurrent new 429 writes.

## Risks / Trade-offs

- **A future OpenAI product introduces a reset more than one year away** → The value falls back to the short Retry-After/backoff path. The one-year margin is far above current monthly windows and can be revised in code/spec if the OpenAI service contract changes.
- **A malformed deadline is ignored while the account is genuinely throttled** → The existing Retry-After parser or minimum persisted backoff still prevents immediate peer re-selection.
- **An existing poisoned row has no fresh usage evidence** → It remains rate-limited after the minimum floor until ordinary recovery evidence arrives; the fix does not manufacture quota availability.
- **Concurrent recovery races with a new OpenAI service block** → Existing compare-and-set guards reject the stale recovery write.

## Migration Plan

Deploy normally with no schema change. New malformed metadata is rejected immediately. Existing implausible rows become recoverable on their next selection or usage-refresh evaluation. Rollback restores the old interpretation but does not alter stored rows.

## Open Questions

None.
