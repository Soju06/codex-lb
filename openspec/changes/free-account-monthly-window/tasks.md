## 1. Backend normalization and storage

- [x] 1.1 Add a shared normalization path for rate-limit and usage payloads that recognizes `primary_window.limit_window_seconds == 2592000` with `secondary_window == null` as a monthly-only free-account window.
- [x] 1.2 Update free-account quota capacity and summary calculations so the 30d monthly window carries free quota capacity and the 7d window contributes zero free quota capacity.
- [x] 1.3 Remove the current logic that infers weekly secondary semantics solely from `primary.limit_window_seconds == 604800`.
- [x] 1.4 Extend backend account/dashboard/API-facing quota models and mappers to expose monthly-only free-account quota without synthesizing 5h or 7d windows.

## 2. Migration

- [x] 2.1 Add an Alembic migration that rewrites legacy free-account `usage_history.window` values from `primary` / `secondary` to `old-primary` / `old-secondary`.
- [x] 2.2 Add migration coverage or validation proving non-free account rows remain unchanged.

## 3. Frontend account and dashboard presentation

- [x] 3.1 Update account cards, account list rows, and account usage panels to show only `Monthly` for normalized monthly-only free accounts.
- [x] 3.2 Update account trend labels and legends so monthly-only free accounts remain visible in the 7-day trend view with correct monthly labeling.
- [x] 3.3 Update overview, nav progress, and donut logic so free monthly accounts use 30d semantics and zero-credit assigned accounts are omitted from 5h/weekly donut totals.

## 4. Frontend API-key assigned-account UI

- [x] 4.1 Update API-key create/edit assigned-account selection badges and chips so free monthly accounts show `Monthly <percent>% left` and do not show weekly-left badges.
- [x] 4.2 Preserve existing `5h` and `7d` badge behavior for paid accounts.

## 5. Tests

- [x] 5.1 Add or update backend tests covering monthly-only free-account normalization, free monthly quota capacity, and removal of the weekly-primary shortcut.
- [x] 5.2 Add or update migration tests covering free-account legacy row renames and non-free row stability.
- [x] 5.3 Add or update frontend tests covering monthly-only account surfaces, trend legends, donut/progress behavior, and API-key assigned-account badges.

## 6. Verification

- [x] 6.1 Run focused backend test commands for usage refresh, quota mapping, proxy rate-limit payloads, and the new migration.
- [x] 6.2 Run focused frontend test commands for account surfaces, dashboard quota logic, and API-key assigned-account components.
- [x] 6.3 Run repo lint/typecheck or equivalent validation required by the touched backend and frontend paths.
