# Proposal: add-weekly-token-pace

## Why

The dashboard shows weekly quota remaining, but it does not show whether the pool is being spent faster or slower than the time left until each account's weekly reset. Comparing average account percentages is misleading when accounts have different capacities or different reset deadlines, and raw request tokens are not the same unit as ChatGPT weekly quota credits.

## What Changes

- Add a weekly pace card to the dashboard.
- Compute pace from weekly credit budget totals, not average account percentages.
- For each account, compare actual weekly credits remaining with expected credits remaining at that account's own reset deadline, then sum the credit values and derive display percentages from the totals.

## Capabilities

### Modified Capabilities

- `frontend-architecture`
