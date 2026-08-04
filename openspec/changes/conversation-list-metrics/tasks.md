# Conversation list metrics tasks

- [x] Implement backend conversation aggregation with eligible-row filtering,
  `COUNT(*)`, `MIN(requested_at)`, and the existing latest-request metric.
- [x] Update API schema and service mapping to expose `requestCount` and
  `firstRequest` while preserving `lastRequest`.
- [x] Update the frontend schema and duration formatter for the grouped metrics
  and the required zero, sub-day, and multi-day formats.
- [x] Render the exact conversation-list column order and top-aligned
  conversation-ID cells.
- [x] Add focused backend and frontend regression tests for aggregation,
  formatting, column order, and alignment.
- [x] Run `openspec validate --specs` and
  `openspec validate conversation-list-metrics --type change --strict`.

No task is complete until its verification command passes.
