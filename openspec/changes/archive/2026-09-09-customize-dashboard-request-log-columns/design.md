## Context

The dashboard renders request logs through `RecentRequestsTable`, with a fixed
set of columns and browser-managed horizontal overflow. The feature is entirely
presentational and requires no backend contract changes.

## Goals / Non-Goals

**Goals:**

- Let operators select visible request-log columns from the existing dashboard.
- Let pointer and keyboard users resize each visible column independently.
- Persist and safely restore the layout per browser.
- Preserve existing request filtering, pagination, row details, and defaults.

**Non-Goals:**

- Creating another dashboard route or navigation item.
- Changing request-log APIs, schemas, database records, or server settings.
- Synchronizing layout preferences between browsers or users.

## Decisions

- Keep column metadata and bounded default widths in one typed frontend module
  so the chooser, table, and preference validation share a single source of
  truth.
- Store only column identifiers and widths in versioned `localStorage`.
  Defensive parsing ignores unknown columns and malformed widths.
- Extend `RecentRequestsTable` with optional presentation props. Its existing
  defaults remain all columns, preserving other callers and tests.
- Render an accessible separator in each visible header. Pointer movement sets
  the width continuously; Left/Right arrow keys adjust it by a fixed step.
- Set table minimum width to the sum of visible widths so the existing
  horizontal scroll container handles overflow without a global width slider.

## Risks / Trade-offs

- Browser-local settings can become stale after future columns are added.
  → Validate stored identifiers and provide a restore-default action.
- Very wide user-selected columns require horizontal scrolling.
  → Keep bounded widths and retain the existing overflow container.
- Drag handles can interfere with header content.
  → Restrict pointer behavior to a narrow trailing-edge separator with an
  explicit resize cursor and accessible name.
