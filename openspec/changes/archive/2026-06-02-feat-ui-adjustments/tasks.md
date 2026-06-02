## 1. Donut Center Layout

- [x] 1.1 Split credits center into stacked remaining (top) and capacity (bottom) rows separated by a divider.
- [x] 1.2 Add `data-testid="donut-center-remaining"` and `data-testid="donut-center-capacity"` to the new elements.
- [x] 1.3 Remove old `data-testid="donut-center-fraction"`.

## 2. Donut Title

- [x] 2.1 Rename primary donut title from `"Hourly Credits"` to `"5-Hour Credits"`.

## 3. Account Summary Sort

- [x] 3.1 Sort `account_summaries` in `DashboardService.get_overview` by `capacity_credits_primary` descending.

## 4. Account Card Height

- [x] 4.1 Reduce `ACCOUNT_CARD_ROW_HEIGHT_REM` from 12.5 to 11.5.

## 5. Weekly Pace Header

- [x] 5.1 Remove `items-center` from the weekly credits pace card header row.

## 6. Tests

- [x] 6.1 Update `usage-donuts.test.tsx` to assert stacked remaining/capacity values and `"5-Hour Credits"` title.
- [x] 6.2 Update `account-cards.test.tsx` to assert new `maxHeight` with 11.5rem row height.

## 7. Spec Delta

- [x] 7.1 Add `openspec/changes/feat-ui-adjustments/specs/frontend-architecture/spec.md`.
