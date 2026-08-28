## Why

Operators can choose between several routing strategies, but the dashboard and README do not explain when each one is appropriate. That makes it easy to pick a strategy that works against the intended pool behavior.

## What Changes

- Add operator-facing guidance for each routing strategy in the README.
- Surface a compact strategy guide in the Routing settings UI below the strategy selector.
- Include a safety note for strategies that intentionally concentrate traffic.

## Impact

- README operator guidance.
- Dashboard Routing settings UI and i18n strings.
- Frontend regression coverage for the rendered guide.
