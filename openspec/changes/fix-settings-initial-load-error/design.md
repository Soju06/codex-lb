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

1. **Reuse the `ApisPage` three-branch shape verbatim.** The branch order
   becomes pending-skeleton, then failed-load error, then the form. Copying the
   established shape keeps Settings consistent with APIs, Conversations, and the
   Dashboard request-log section instead of inventing a fourth error layout.

2. **Scope the skeleton with `isPending`, not with error absence.** The skeleton
   condition is `settingsQuery.isPending && !settings`. TanStack Query keeps
   `isPending` true only while no data and no error exist for the query, so this
   exits the skeleton on the failure edge without a separate error check, and it
   still covers a refetch that has no cached data.

3. **Route the settings error to exactly one place.** The page-level aggregate
   alert drops `settingsQuery.error` when `settings` is absent, because the
   failed-load branch renders that same message. Otherwise the message would
   render twice on a failed first load. When `settings` is present the aggregate
   alert keeps owning it, which is what preserves today's cached-data behavior.

4. **Retry uses the query's own `refetch` and `isFetching`.** Disabling Retry
   while `isFetching` is what makes the control deterministic to drive from a
   test and prevents a queued burst of refetches on repeated clicks.

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

## Migration Plan

No data migration and no API change. Operators on a healthy Settings page see no
difference; only the previously stuck failure path changes.
