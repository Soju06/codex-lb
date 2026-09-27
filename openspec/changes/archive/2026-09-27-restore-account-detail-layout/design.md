## Context

AccountListItem and AccountDetail still implement the original compact account selector and selected-account statistics/charts/actions. They can be composed alongside the overview modes without copying management code.

## Goals / Non-Goals

**Goals:** Restore the original layout, retain optional overviews, keep shared filter/selection state and subscription information.

**Non-Goals:** Redesign the original account selector, change chart/management behavior, alter account API or deploy.

## Decisions

- Add `detail` to the existing locally stored view enum. Missing or invalid preferences select Detail; valid List/Grid preferences remain honored.
- Detail uses the original responsive two-column widths, AccountListItem rows in an internally scrolling selector and inline AccountDetail. On mobile the selector and detail stack vertically.
- Keep one AccountList mounted across modes to retain search/status/sort and overview page. Detail renders the full compact filtered list; List and Grid remain bounded at 24 summaries.
- Selection changes the inline detail in Detail mode and opens a dialog in overview modes. Only the selected inline or open dialog account fetches trends/credit details.
- Existing selected-account URLs work in all modes. Changing modes closes the overview dialog while retaining selection.

## Risks / Trade-offs

- Detail intentionally retains the old full compact selector; rich overview rendering remains paginated.
- Mobile original layout places charts below the selector, matching the previous layout.
