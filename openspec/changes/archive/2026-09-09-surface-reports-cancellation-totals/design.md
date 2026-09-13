# Design: Surface reports cancellation totals

## Context

The raw Reports backend models define cancellation data as `ReportSummary.total_cancelled` and `DailyReportRow.cancelled_count`. Dashboard API serialization exposes those fields as `summary.totalCancelled` and `daily[].cancelledCount`, which are also the names consumed by the frontend. The frontend's strict response schemas omit both camelCase properties, so parsing strips the values before the report model reaches rendering and export. The date-range completion path also creates synthetic daily rows without a cancellation field. As a result, cancellation data is absent from the summary, daily table, and downloaded CSV despite being available at the system boundary.

This change is limited to the Reports frontend. The existing `usage-error-metrics` specification remains the owner of request terminal classification and cancellation accounting.

## Goals and Non-goals

### Goals

- Preserve the backend cancellation fields through frontend parsing.
- Treat a synthesized no-activity day as having zero cancellations.
- Present cancellations beside requests and errors in the visible summary, table, and CSV.
- Keep labels localized, including English, Korean, and Simplified Chinese.
- Prove existing request and error values do not regress.

### Non-goals

- Changing backend aggregation, response casing, storage, or terminal classification.
- Recomputing cancellations in the browser.
- Redesigning the Reports page or introducing a new visual primitive.

## Decisions

### Preserve cancellation values in the typed report model

The response schemas will explicitly parse `summary.totalCancelled` and each `daily[].cancelledCount`. The UI and export paths will consume these parsed fields rather than deriving cancellation counts from requests and errors. Derivation would be incorrect because requests may include successful, cancelled, and genuinely failed terminals, and future terminal classes may exist.

### Zero-fill only synthesized empty days

The date-range completion path will assign `cancelledCount: 0` to synthetic rows, matching the existing zero-fill semantics for other count metrics. A cancellation value returned by the API will be preserved, including an explicit zero.

### Extend existing Reports presentation patterns

The cancellation summary item and daily-table column will compose the page's existing summary and table primitives, spacing, typography, responsive behavior, and semantic design tokens. The CSV will add a localized cancellation header in the same column order used by the visible daily table. No one-off visual values or separate desktop/mobile markup will be introduced.

### Use the existing localization boundary

Visible labels and the CSV header will use the Reports translation namespace. English, Korean, and Simplified Chinese resources will receive equivalent cancellation labels; CSV generation will use the active locale just as existing headers do.

## Failure Modes and Mitigations

- **Schema strips valid backend data:** parser tests assert both cancellation fields survive parsing.
- **Synthetic rows expose `undefined` or a blank CSV cell:** zero-fill tests assert a numeric `0` in the model, table, and export.
- **Cancellation is accidentally folded into errors:** regression fixtures retain distinct requests, cancellations, and errors and assert all three independently.
- **A label is readable in one locale only:** locale coverage and real zh-CN browser evidence verify the visible label and CSV header.
- **The added column overflows or hides key values:** desktop and 390px mobile browser evidence verifies the existing responsive table behavior with the added column.
- **QA leaves local state behind:** the browser QA task records teardown of servers, ports, browser sessions, downloads, fixtures, and temporary data.

## Example

Given a parsed frontend report response with `totalRequests: 4`, `totalCancelled: 2`, and `totalErrors: 1`, plus a daily row with `requests: 4`, `cancelledCount: 2`, and `errorCount: 1`, parsing preserves all six values. The summary visibly shows Requests 4, Cancelled 2, and Errors 1; the daily table shows the same breakdown; and the localized CSV contains a cancellation column with value `2`. A missing date synthesized into the selected range displays and exports cancellation value `0`.
