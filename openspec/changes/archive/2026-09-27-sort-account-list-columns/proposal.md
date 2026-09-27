## Why

Operators need to compare Accounts List rows by plan, remaining subscription and each quota window, and see available reset counts without opening account details.

## What Changes

- Add ascending/descending sorting by plan name, recorded subscription deadline, remaining 5h quota and remaining 7d quota.
- Make desktop List headers clickable sort controls with visible and accessible direction; expose the same modes in the existing sort selector for mobile.
- Add a small available-reset-count badge to List rows, following the existing reset-badge visibility setting.
- Keep unknown values last, apply sorting before pagination and retain shared sort state across account views.

## Capabilities

### New Capabilities
- None.

### Modified Capabilities
- `frontend-architecture`: Extend Accounts sorting and define List column controls.

## Impact

Frontend account sorting, List headers, locale strings, regression tests and screenshots. No API, backend, deployment or new setting.
