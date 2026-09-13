## Why

Quota refreshes can briefly omit a percentage, which makes account bars drain
to zero before the next snapshot arrives. Whole-number rendering and a fixed
30-second dashboard poll also make valid changes look abrupt.

## What Changes

- Keep the last known account percentage through a bounded unknown refresh and
  animate later values at 0.1 percent display resolution.
- Add a local dashboard refresh preference of 5, 15, 30, or 60 seconds, with a
  15-second default.
- Preserve raw provider percentages for sorting, routing, and API responses.

## Capabilities

### Modified Capabilities

- `account-quota-presentation`: stable, higher-resolution quota presentation.
- `frontend-architecture`: configurable dashboard polling.

## Impact

- Dashboard presentation only.
- No routing eligibility, account status, or provider quota value changes.
