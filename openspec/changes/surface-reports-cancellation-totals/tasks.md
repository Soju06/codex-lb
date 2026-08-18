# Tasks

## 1. Deterministic regression coverage

- [x] 1.1 Add parser tests proving `summary.totalCancelled` and `daily[].cancelledCount` survive reports response parsing.
- [x] 1.2 Add date-range completion coverage proving a synthesized daily row has `cancelledCount: 0`.
- [x] 1.3 Add summary and daily-table tests proving Requests 4, Cancelled 2, and Errors 1 remain distinct and visible.
- [x] 1.4 Add CSV coverage proving the localized cancellation header, cancellation value `2`, zero-filled value `0`, and existing request/error values.
- [x] 1.5 Run the focused parser/table/export tests before implementation and record the expected cancellation-specific failures.

## 2. Reports cancellation presentation

- [x] 2.1 Extend the typed Reports schemas and zero-fill model to preserve cancellation values.
- [x] 2.2 Add a cancellation item to the existing Reports summary composition.
- [x] 2.3 Add a cancellation column to the existing daily detail table and CSV export.
- [x] 2.4 Add equivalent Reports cancellation labels for every supported locale, including English, Korean, and Simplified Chinese.
- [x] 2.5 Re-run the focused tests and frontend diagnostics, then run the affected frontend test, type-check, lint, and build gates.

## 3. User-visible verification and cleanup

- [x] 3.1 Exercise a real Reports fixture with Requests 4, Cancelled 2, and Errors 1 in Chromium and verify the visible summary, daily table, and downloaded CSV.
- [x] 3.2 Capture desktop, 390px mobile, and zh-CN evidence showing cancellation alongside unchanged request and error values.
- [x] 3.3 Verify a zero-filled date visibly shows and exports cancellation value `0`.
- [x] 3.4 Tear down browser sessions, frontend/backend processes, ports, fixture databases, downloads, and temporary QA artifacts, and record the cleanup receipt.
- [x] 3.5 Run `openspec validate surface-reports-cancellation-totals --strict` and retain the exact successful output.
