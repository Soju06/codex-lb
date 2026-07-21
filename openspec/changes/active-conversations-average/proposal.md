## Why

The dashboard and report conversation cards are labeled `Conversations`,
while the dashboard request total includes requests with no conversation ID.
Operators need an Active Conversations label and an average that measures only
requests belonging to conversations.

## What Changes

- Rename the dashboard and report conversation card labels to Active Conversations.
- Expose the count of non-warmup requests with a nonblank conversation ID in
  the dashboard metrics response.
- Show `Avg req/conv` on the dashboard conversation card as that count divided
  by the distinct conversation count for the selected timeframe.

## Capabilities

### New Capabilities

(none)

### Modified Capabilities

- `frontend-architecture`

## Impact

- **Code:** request-log aggregation, dashboard metric schemas/builders, and
  dashboard/report translations and tests.
- **API:** one additive `summary.metrics.conversationRequests` field in the
  dashboard overview response.
- **Compatibility:** existing request and conversation fields retain their
  meanings; missing additive fields default safely to zero in the frontend.
