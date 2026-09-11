## Context

First-run operators land on empty Accounts, APIs, Dashboard, and Reports
surfaces. Automations already splits first-run vs filtered empty copy. Settings
keeps Advanced collapsed by default so first-paint skips firewall/quota
self-fetches. The legacy `/firewall` route only redirects to `/settings`.

## Goals / Non-Goals

**Goals:**

- First-run empty lists describe setup, not filter mismatch.
- Filtered-empty lists keep "no matches / adjust filters" copy.
- Dashboard empty-account surfaces link to `/accounts`.
- Reports line charts with no daily rows show a no-data state instead of a
  zero-filled series.
- `/firewall` expands Advanced and scrolls to the firewall section.
- Plain `/settings` stays collapsed by default.

**Non-Goals:**

- Guest write-gating, infinite skeletons, or session-fail-as-admin.
- Conversation empty-copy, donut empty-copy, or daily-table zero-fill changes.
- New Settings query parameters beyond `advanced=1` and the existing `#firewall`
  hash.
- API, schema, or nav-budget changes.

## Decisions

1. **Reuse the Automations empty-vs-filtered key pattern.** Lists key off
   source-array emptiness (`accounts.length === 0`), not only the filtered
   array. Request logs take a `filtersApplied` flag from non-default filters
   (search, timeframe other than `all`, account/API-key/model/status
   selections, or conversation pin).
2. **Optional EmptyState action slot.** Dashboard empty-account cards and list
   render a `Link` to `/accounts`. Accounts/APIs already have add/create
   buttons above the list, so they only change copy.
3. **Reports no-data uses the raw `daily` payload.**
   `buildContinuousDailyRows` still fills gaps when any daily row exists.
   Empty `data` skips the chart.
4. **Firewall deeplink is a redirect target, not a new page.**
   `/firewall` → `/settings?advanced=1#firewall`. Advanced opens when
   `advanced=1` or the hash is `#firewall`. After mount, scroll to
   `id="firewall"`. Unmount-while-collapsed is unchanged for plain `/settings`.

## Risks / Trade-offs

- Opening Advanced on the deeplink issues the same self-fetching section
  requests as a manual expand. Acceptable: the operator asked for firewall.
- `filtersApplied` treats any non-default request-log filter as filtered-empty,
  including a narrowed timeframe on a brand-new fleet. That is correct: the
  operator is looking at a filter, not first-run.
- Hash scroll depends on the firewall section mounting after expand. Tests
  cover the heading becoming visible; scroll is best-effort `scrollIntoView`.

## Migration Plan

No data migration. Existing `/firewall` bookmarks keep working with a richer
target. Operators on `/settings` see no change.
