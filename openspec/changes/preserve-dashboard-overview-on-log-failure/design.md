## Context

`DashboardPage` starts overview, projections, request-log options, and request-log listing queries independently, but its composed `view` currently requires both overview and request-log data. A terminal listing error therefore leaves the full `DashboardSkeleton` mounted even after the healthy overview path has completed. The request-log query already exposes a local `refetch` operation, and existing shared loading, alert, and button components cover the required states without a new API or dependency.

## Goals / Non-Goals

**Goals:**

- Keep successful overview, quota, projection, and account content usable when the initial request-log listing fails.
- Keep loading and terminal failure for the initial listing inside the Request Logs section.
- Recover through the existing request-log query's `refetch` operation without invalidating or hiding healthy overview data.
- Prove failure and recovery through the real `/dashboard` App route and production query retry policy.

**Non-Goals:**

- Preserve stale request-log rows after a later refetch failure.
- Change request-log APIs, retry policy, polling, caching, indexes, or backend reliability.
- Split routes, redesign layout, add global state/live-region semantics, or add settings, navigation, or dependencies.

## Decisions

1. **Compose overview-backed view data as soon as overview exists.** Pass an empty request array to the existing view builder until listing data is available, then render the Request Logs section according to the listing query state. This deepens the current page composition without creating a second view model. The rejected alternative—splitting the view builder or adding a new cross-query abstraction—would add surface area without changing the independent query contract.

2. **Keep the failure boundary at the Request Logs section.** Initial pending state renders the shared scoped loading treatment; terminal listing error renders the shared error alert inside a section-local alert semantic plus the existing localized Retry button; ready state renders filters and table. The rejected alternative—continuing to aggregate the listing error at page level—would preserve the outage coupling this change removes.

3. **Retry through `logsQuery.refetch` only.** The local control does not call broad Dashboard invalidation, so overview/projection requests and healthy content remain untouched. The header refresh action retains its existing broad behavior.

4. **Cover the real route with MSW.** The regression uses `<App />`, the production `queryClient` (`retry: 1`), real hooks/API schemas, and call counters. It seeds unique values for a statistic, quota surface, projection metric, and account control; switches the listing handler from 500 to a deliberately pending 200 response; keyboard-activates the native Retry control; and asserts overview request count and visible healthy content remain stable throughout recovery.

## Risks / Trade-offs

- Passing an empty request array while the listing is unavailable means the shared view model briefly contains no request rows, but the table is not rendered until the listing is ready; this avoids exposing a false empty state.
- Request-log option failures remain governed by their existing error path; this change isolates only the listing failure proven by the regression.
- Later refetch failures with existing rows are intentionally not specified as stale-row preservation and can be addressed separately.
