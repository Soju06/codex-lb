## Context

The backend overview response already carries a nullable `cancelledCount`.
`DashboardOverviewSchema` is the frontend trust boundary, and Zod strips
unknown object keys there. The omission is therefore isolated to the typed
consumer contract; no calculation or transport change is required.

## Goals / Non-Goals

**Goals:**

- Keep the frontend overview contract aligned with the backend response.
- Lock the documented requests/error/cancelled breakdown with a focused test.

**Non-Goals:**

- Change cancellation classification or aggregation.
- Add a new dashboard card or navigation surface.
- Change backward compatibility for payloads that omit the field.

## Decisions

- Declare `cancelledCount` as nullable and optional, matching the additive
  backend response and preserving compatibility with older servers.
- Test the field through `DashboardOverviewSchema.parse`, the actual API
  boundary, rather than testing the nested schema in isolation.

Alternative considered: configure the metrics object with `.passthrough()`.
That would weaken the trust boundary for every unknown metric, so the explicit
field is the smaller and safer change.

## Risks / Trade-offs

- [Risk] Frontend and backend optionality drift → Mirror the existing additive
  metric pattern and cover a payload that includes the field.

## Migration Plan

Ship as an additive frontend contract change. Rollback is removal of the field;
backend responses remain compatible in either direction.

## Open Questions

None.
