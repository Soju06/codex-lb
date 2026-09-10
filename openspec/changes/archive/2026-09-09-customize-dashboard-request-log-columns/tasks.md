## 1. Column layout model

- [x] 1.1 Add typed request-log column metadata, default widths, and width bounds.
- [x] 1.2 Add a versioned browser-local preference hook with defensive parsing, persistence, final-column protection, and restore-default behavior.
- [x] 1.3 Add focused preference tests for persistence, malformed data, bounds, and reset behavior.

## 2. Resizable request-log table

- [x] 2.1 Extend `RecentRequestsTable` with optional visible-column and column-width props while preserving all-column defaults.
- [x] 2.2 Render only selected headers and cells, and derive table minimum width from visible column widths.
- [x] 2.3 Add accessible pointer and keyboard resize separators to visible headers.
- [x] 2.4 Add component tests for visibility, pointer resizing, keyboard resizing, bounds, and horizontal overflow sizing.

## 3. Dashboard integration

- [x] 3.1 Add the column chooser and restore-default action to the existing dashboard Request Logs section.
- [x] 3.2 Connect saved visibility and width preferences to `RecentRequestsTable` without changing filters, pagination, or account/dashboard content.
- [x] 3.3 Add dashboard integration coverage for column selection, resizing, persistence, and absence of a global width control.

## 4. Validation

- [x] 4.1 Run frontend type checking, lint, focused tests, and the full frontend suite.
- [x] 4.2 Run the production frontend build and strict OpenSpec validation.
- [x] 4.3 Review the final diff to confirm it contains no Compact route, navigation, backend, deployment, secret, or machine-specific changes.
