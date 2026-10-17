## Context

See proposal.md. Main's twelve-hour gate rotates idle refresh material, whereas `should_refresh()` governs request access-token freshness. Their distinct purposes require distinct intervals.

## Goals / Non-Goals

**Goals:** preserve idle and paused keepalive, make the fixed guardian threshold explicit, retain the fresh-row recheck and batch semantics.

**Non-Goals:** alter request refresh policy, add operator tuning, remove leader coordination, or claim scan cadence guarantees every eligible account is immediately admitted.

## Decisions

Use a fixed twelve-hour constant in guardian.py and the existing UTC-normalized predicate at both selection and recheck. Keep the already-removed constructor override removed: tests control the injected clock instead of changing the runtime policy. Forced refresh still executes only after the fresh row is due.

Keep the earlier archived proposal as historical evidence, but mark it superseded by this change. The main spec, stable context, and live UI describe the implemented twelve-hour gate. Preserve unrelated context from current main.

## Risks / Trade-offs

- Refresh tokens may fail before admission under large pools or active backoff; oldest-first bounded work and request-time recovery remain, without an unsupported deadline guarantee.
- Exact boundary is strictly older than twelve hours, matching main; future timestamps are ineligible and timezone-aware values are normalized.
- A peer can refresh between selection and execution; the fresh-row recheck suppresses redundant exchange.

## Example

An idle account refreshed Monday at 00:00 is not eligible at 12:00 exactly. At the next six-hour scan after it crosses twelve hours it is considered, and rotates only if admitted within the oldest 100 non-backoff accounts. It need not receive traffic or wait eight days.
