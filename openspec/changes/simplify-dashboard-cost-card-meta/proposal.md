## Why

The dashboard `Est. API Cost` card currently repeats two pieces of information in its meta line: the card title already says the value is estimated, and the cached-token count already appears on the Tokens card. That makes the cost card harder to scan without adding distinct cost-specific meaning.

## What Changes

- Update the dashboard overview `Est. API Cost` card meta text to show only the average cost for the selected timeframe.
- Keep the existing card title, total value, and comparison indicator behavior unchanged.
- Leave the Tokens card's cached-token metadata unchanged.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `frontend-architecture`: The dashboard overview requirements will define the `Est. API Cost` card meta text as the averaged cost only, without repeating estimate wording or cached-token counts.

## Impact

- Frontend dashboard view-model formatting in `frontend/src/features/dashboard/utils.ts`
- Frontend tests covering dashboard stat metadata in `frontend/src/features/dashboard/utils.test.ts`
- Frontend architecture OpenSpec requirements for dashboard summary-card copy
