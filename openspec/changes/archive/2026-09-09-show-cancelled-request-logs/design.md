## Context

Cancellation persistence and aggregate accounting are already correct:
downstream disconnects are stored with `status='cancelled'`, and shared usage
logic classifies them as non-errors. The defect begins in the Request Logs
read path. Its filter model has booleans for success and error rows only, so
the raw query, filter facets, count cache, and demand-rollup count all omit
cancelled rows. If a cancelled row were allowed through independently, the
mapper would currently expose it as `error`.

The frontend already transports arbitrary repeated status query values and
builds filter options from the server response. It needs only a localized,
visually distinct cancelled presentation once the backend exposes the status.

## Goals / Non-Goals

**Goals:**

- Keep the unfiltered list, filtered list, status facet, and displayed total
  aligned for cancelled rows.
- Preserve the invariant that cancelled and error are separate terminal
  statuses.
- Reuse the existing Request Logs filter and badge primitives.

**Non-Goals:**

- Change cancellation persistence or proxy disconnect handling.
- Change error/cancellation aggregate metrics, Reports, or live usage.
- Add a migration, setting, navigation item, or new dashboard primitive.
- Backfill or rewrite historical request logs.

## Decisions

### Thread an explicit cancelled inclusion flag through the existing read path

Add `include_cancelled` beside the existing success/error filter fields. The
default and `all` paths enable it; the explicit `cancelled` filter enables
only it; the explicit `error` filter continues to match persisted error rows
excluding rate-limit and quota codes.

The flag also participates in the count-cache key and
`_DemandCountParams`. The demand-rollup count adds the persisted
`cancelled` status when enabled. This keeps pagination totals equal to the
rows that can actually be listed across folded and raw windows.

Alternative considered: classify cancelled rows under the existing error
branch. That contradicts the canonical non-error contract and would make the
error filter semantically wrong.

### Preserve additive frontend compatibility

Keep the existing string-based API and URL status schemas. They intentionally
permit a mixed-version frontend to transport an unfamiliar additive status.
Add the known `cancelled` label, locale entries, and badge class without
turning the boundary into a closed enum.

Alternative considered: replace status strings with a closed Zod enum. That
would reject future additive statuses and break the existing stale-filter
recovery behavior.

### Reuse the current badge primitive

Use the existing outline badge and its status-class map with a neutral sky
treatment distinct from success, rate-limit, quota, and error. No design token
or reusable component is introduced.

## Risks / Trade-offs

- [Risk] Raw rows become visible while folded totals remain too small
  → Include cancelled in the demand-rollup status filter and count-cache
  signature; cover total and filter behavior through the public API.
- [Risk] Cancelled leaks into the error filter
  → Seed both cancellation and genuine error controls and assert each explicit
  filter independently.
- [Risk] Mixed-version status values stop parsing
  → Retain open string schemas and add presentation only for the known value.

## Migration Plan

Ship as an additive read-path and presentation change. Rollback restores the
previous omission without changing stored data or schema.

## Open Questions

None.
