## Why

The dashboard weekly credits pace card currently mixes two different concepts: current linear-schedule variance and a future shortfall forecast. It also derives forecast burn from the average consumed across the entire weekly window, so old bursts dominate the card even when all active accounts have fresh data and recent load is low. Deactivated or stale usage rows can also remain in the frontend input and be advanced into a future reset cycle, producing false plan and recovery numbers.

## What Changes

- Move weekly credits pace calculation to the dashboard backend where account status, usage freshness, and usage history are available together.
- Include only active accounts with fresh weekly usage samples in the pace pool; stale or inactive rows are excluded and counted for operator context.
- Keep the current schedule comparison as a separate `scheduleGapCredits` value.
- Compute forecast/recovery from recent weekly usage slope (EWMA over history), not the full-window cumulative average.
- Surface forecast burn rate, scheduled burn rate, projected shortfall, and confidence metadata so the UI can label the card accurately.
- Keep frontend fallback calculation for older responses, but prefer the server-provided weekly pace contract.

## Impact

- Dashboard `/api/dashboard/overview` gains an optional `weeklyCreditPace` object.
- The weekly pace card copy changes from ambiguous future-shortfall wording to explicit schedule-gap and forecast wording.
- No routing behavior changes.
