## 1. Dashboard account view mode

- [x] 1.1 Add a local dashboard preference for account view mode with `cards` as the default.
- [x] 1.2 Add a compact Accounts section view-mode control.
- [x] 1.3 Render the existing account cards when card mode is selected.
- [x] 1.4 Render a compact list view with account identity, status, quota, credits, warm-up, and actions when list mode is selected.
- [x] 1.5 Add compact visual quota meters to the list view.
- [x] 1.6 Add clickable list headers for account, status, plan, quota, credits, and warm-up sorting.
- [x] 1.7 Persist the selected list sort column and direction locally.

## 2. Validation

- [x] 2.1 Add/update focused frontend tests for card/list mode and preference persistence.
- [x] 2.2 Run targeted dashboard/preference tests.
- [x] 2.3 Run frontend lint and typecheck.
- [x] 2.4 Validate the OpenSpec change with `openspec validate add-dashboard-account-list-view --strict` if the CLI is available.
