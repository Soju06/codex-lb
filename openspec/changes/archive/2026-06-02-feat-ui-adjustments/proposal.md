## Why

The dashboard donut center previously rendered remaining credits and capacity as a single inline fraction (`7,331/7,560`). Operators reported that the fraction was hard to scan quickly and the donut titles said "Hourly Credits" which was ambiguous. Additionally, the account cards list did not sort by capacity, making it harder to spot the largest accounts at the top.

## What Changes

- `frontend/src/components/donut-chart.tsx`: split the credits center layout into two stacked rows — remaining on top, a thin divider, capacity below — instead of an inline `remaining/total` fraction. Add `data-testid="donut-center-remaining"` and `data-testid="donut-center-capacity"` for test targeting.
- `frontend/src/features/dashboard/components/usage-donuts.tsx`: rename the primary donut title from `"Hourly Credits"` to `"5-Hour Credits"` to match the 5-hour quota window wording used elsewhere.
- `app/modules/dashboard/service.py`: sort account summaries by `capacity_credits_primary` descending so the highest-capacity accounts appear first.
- `frontend/src/features/dashboard/components/account-cards.tsx`: reduce `ACCOUNT_CARD_ROW_HEIGHT_REM` from 12.5 to 11.5 to tighten the card viewport.
- `frontend/src/features/dashboard/components/weekly-credits-pace-card.tsx`: remove `items-center` from the header row so the title and gauge icon align to the flex start instead of centering vertically.

## Capabilities

### Modified Capabilities

- `frontend-architecture`: dashboard donut center layout, donut title, account card height, weekly pace header alignment.
- Backend: dashboard account summary sort order.

## Impact

- **Code**: `donut-chart.tsx`, `usage-donuts.tsx`, `dashboard/service.py`, `account-cards.tsx`, `weekly-credits-pace-card.tsx`.
- **Tests**: `usage-donuts.test.tsx` (updated assertions for stacked layout and title rename), `account-cards.test.tsx` (updated height expectation).
- **API/back-end**: account summaries in the overview response are now sorted by primary capacity descending.
- **Operational**: no migration, no settings.
