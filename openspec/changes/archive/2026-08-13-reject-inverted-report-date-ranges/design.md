## Context

The Reports service applies date defaults, computes an inclusive window, and then performs several aggregate repository calls. A negative window is currently clamped to one day after the existing 730-day guard, so inverted bounds proceed through the repository and serialize as a plausible empty report. The dashboard keeps both date fields in page state, gives each only a `max` of the browser-local current day, and starts both the filtered and relaxed-catalog Reports queries for every state.

The correction crosses the Reports service/API boundary and the Reports filter/query UI. It must preserve the existing inclusive 730-day guard, timezone conversion, presets, aggregates, and repository implementation. PR #1325 currently owns `app/modules/reports/repository.py`, so this change does not modify that file.

## Goals / Non-Goals

**Goals:**

- Make date ordering an authoritative Reports domain invariant applied after defaults and before repository I/O.
- Return the exact dashboard 400 envelope with stable code `invalid_report_date_range`.
- Prevent routine inverted selections with reciprocal native date bounds.
- Keep bypassed invalid state understandable to sighted and assistive-technology users.
- Disable both Reports queries while invalid and resume them when either bound is corrected.
- Preserve valid one-day and inclusive 730-day ranges.

**Non-Goals:**

- Changing the 730-day maximum, date presets, timezone semantics, aggregation or query behavior, repository code, charts, schemas, migrations, settings, navigation, or authentication.
- Adding a new dashboard setting or API field.

## Decisions

### Validate in the Reports service with a typed domain error

After applying omitted-bound defaults, `ReportsService.get_reports` compares the resulting dates. It raises a dedicated `InvalidReportDateRangeError` before date conversion or any repository await. The Reports route catches that domain error and raises `DashboardBadRequestError` with code `invalid_report_date_range`, reusing the registered dashboard envelope handler.

This keeps the invariant authoritative for both HTTP and direct service callers. Route-only FastAPI validation was rejected because direct callers would remain unsafe and because cross-field ordering does not belong to either individual query parameter. Repository validation was rejected because it is too late, would mix domain policy with persistence, and would overlap PR #1325.

### Share one frontend date-order predicate

A small Reports date utility treats a range as inverted only when both bounds are present and `startDate > endDate`. The filters use it for accessible feedback and the Reports data hook uses it as the TanStack Query `enabled` condition. Empty bounds retain existing backend-default behavior; this change does not add unrelated required-field validation.

Passing an `enabled` flag from every caller was rejected because it would allow callers to forget the invariant. Central query gating makes both automatic query flows safe by default. Because TanStack Query manual refetch bypasses `enabled`, the page-level Retry action applies the same predicate before invoking either Reports refetch and still retries Accounts while the range is invalid.

### Use native prevention plus explicit accessible recovery

The start input keeps the browser-local current day as its upper ceiling but narrows to the selected end date when that date is earlier. The end input keeps its current-day maximum and receives the selected start date as its minimum. If typed, restored, test-injected, or otherwise bypassed values remain inverted, both controls expose `aria-invalid` and reference one localized inline corrective message via `aria-describedby`. The message is textual and announced, so the state does not rely on color.

Silently swapping dates was rejected because it would conceal operator input and could query an unintended period. Clearing a bound was rejected because it would discard user input and invoke backend defaults.

### Let TanStack Query provide recovery

While the range is inverted, both Reports queries remain mounted with `enabled: false`, preserving their previous cache state without network traffic. The page-level Retry action refetches Accounts only and does not invoke either Reports refetch in that state. Correcting either bound flips the same queries to enabled, causing one fetch for each distinct query key. Presets remain valid and require no special recovery path.

## Risks / Trade-offs

- Native date constraints can be bypassed by typing, browser restoration, or programmatic state; the explicit error and query gate are therefore required rather than relying on `min`/`max` alone.
- Two controls share one description. This intentionally avoids duplicate announcements while still linking the corrective action from either invalid field.
- A cached prior report may remain in TanStack Query memory while disabled, but the page does not issue an invalid request; the visible inline validation prevents the stale data from being mistaken for a response to the inverted range.
