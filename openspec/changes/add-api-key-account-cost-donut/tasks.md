## 1. Spec
- [x] 1.1 Add an API key delta covering selected-key account cost breakdown visualization.

## 2. Implementation
- [x] 2.1 Add a typed 7-day account usage breakdown endpoint for API keys.
- [x] 2.2 Add a donut chart to the APIs tab and place it beside the resized trend chart.
- [x] 2.3 Ensure known accounts sort by descending cost and `Unknown Account` renders last.
- [x] 2.4 Refine the APIs tab chart ratio, captions, donut legend placement, legend cap, and privacy handling.
- [x] 2.5 Add a request-log index for the API-key account usage query.

## 3. Validation
- [x] 3.1 Add backend coverage for account breakdown ordering and unknown-account grouping.
- [x] 3.2 Add frontend coverage for donut legend labels and layout data.
- [x] 3.3 Run targeted backend and frontend tests.
- [x] 3.4 Validate specs locally with `openspec validate --specs`.
