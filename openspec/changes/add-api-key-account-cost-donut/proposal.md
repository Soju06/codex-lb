## Why

Operators can inspect selected API key cost trends, but the APIs tab does not show which upstream accounts contributed to that cost. That makes it harder to spot skewed routing, deleted-account residue, or a single account carrying most of an API key's spend.

## What Changes

- Add a selected API key 7-day account cost breakdown endpoint.
- Show the existing API key trend chart at 75% width on large screens, with a matching caption and description.
- Add a donut chart beside the trend chart at 25% width with account slices sorted by 7-day cost.
- Label slices by account display name and 7-day cost, grouping missing or unassociated rows as `Unknown Account` after known accounts.
- Place the donut legend below the circle, limit it to three account rows, and respect the dashboard account-info privacy toggle for email-like labels.

## Impact

- Affects API key dashboard backend schemas/service/repository and the APIs tab frontend.
- Adds frontend and backend regression coverage for the account-cost breakdown ordering and unknown-account handling.
