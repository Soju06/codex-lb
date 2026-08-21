## Context

`SettingsPage` computes an aggregate `error` string from the settings query, the
upstream-proxy query, and every settings mutation, then renders
`{!settings ? <SettingsSkeleton /> : <>{error ? <AlertMessage .../> : null} ...</>}`.
Because the skeleton branch is chosen purely on data absence, a failed first
load lands in the branch that can never show the alert the component already
computed. The page stays a skeleton until the operator reloads.

`ApisPage` already solved the same problem for `GET /api/keys`: it splits
pending-first-load from failed-first-load and renders an `AlertMessage` plus a
`Retry` `Button` in the failure branch. `ConversationsView` and the Dashboard
request-log section use the same shape. The dashboard capability already
carries a spec requirement for exactly this behavior ("Dashboard overview and
request-log listing fail independently"), including the alert semantic and the
accessibly named Retry action.

## Goals / Non-Goals

**Goals:**

- A failed first Settings load shows why it failed and offers Retry.
- Retry recovers in place, without a browser reload.
- A pending first load still shows the skeleton.
- A fetch error with cached settings still keeps the form visible.

**Non-Goals:**

- Changing `useSettings`, its query key, retry policy, or adding placeholder
  data. The hook already returns `isPending`, `isFetching`, and `refetch`.
- Per-section error isolation inside Settings, or changing how the
  upstream-proxy query and mutation errors are surfaced.
- New page-level error UI primitives. The existing `AlertMessage` and `Button`
  cover this.

## Decisions

1. **Reuse the `ApisPage` three-branch shape, with retry-state retention.** The
   branch order becomes initial pending-skeleton, then failed-load error, then
   the form. Settings additionally retains the failed-load message while its
   no-data Retry is in flight, because TanStack Query returns that refetch to
   `pending`. This keeps the established layout without letting Retry re-enter
   the original stuck-skeleton state.

2. **Scope the skeleton to initial pending only.** The skeleton condition is
   `settingsQuery.isPending && !settings && initialRetryError === null`. An
   initial request has no retained error and uses the skeleton. Retry captures
   the displayed failure before refetching, so the same no-data query becoming
   pending stays on the error branch with its Retry control.

3. **Route the settings error to exactly one place.** The page-level aggregate
   alert drops `settingsQuery.error` when `settings` is absent, because the
   failed-load branch renders that same message. Otherwise the message would
   render twice on a failed first load. When `settings` is present the aggregate
   alert keeps owning it, which is what preserves today's cached-data behavior.

4. **Retry retains its error and uses the query's own `refetch`.** The click
   stores the displayed error until `refetch` settles. Retry is disabled
   immediately from that retained state and remains disabled while
   `isFetching`, which prevents repeated clicks and preserves actionable
   feedback throughout the request.

5. **`role="alert"` wraps the failure message.** `AlertMessage` is a presentational
   `div` with no ARIA role, so the announcement is added at the call site — the
   same way `ConversationsView` does it. The dashboard requirement this mirrors
   demands an announced error, and the failed-load branch appears after the
   initial render, so it needs a live region to be announced at all.

## Risks / Trade-offs

- The `settings ? settingsQuery.error : null` gate means a failed first load no
  longer shows a concurrent upstream-proxy or mutation error in the aggregate
  alert position. That is correct: with no settings loaded there is no form to
  mutate, and the blocking failure is the settings load itself.
- `isPending` is a TanStack Query v5 semantic. If the query later gains
  `initialData` or `placeholderData`, `isPending` goes false immediately and the
  skeleton stops appearing — but so does the empty state it covers, because
  `settings` would then always be defined. The two move together, so the branch
  stays correct.
- The retained error is local presentation state and is cleared when `refetch`
  settles. Query data and error ownership remain with TanStack Query; the local
  state exists only to bridge its pending no-data retry transition.

## Migration Plan

No data migration and no API change. Operators on a healthy Settings page see no
difference; only the previously stuck failure path changes.
