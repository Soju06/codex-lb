## Implementation
- [x] Extract durable snapshot storage, fenced producer and snapshot-only HTTP reads onto current upstream.
- [x] Preserve current dashboard and Reports contracts without averaging controls or unrelated UI changes.
- [x] Add freshness states and bounded automatic retries.

## Verification
- [x] Add migration, lifecycle, permissions, report and frontend regressions in a separate test commit.
- [x] Validate OpenSpec, static checks and affected backend/frontend consumers.
- [x] Measure matched before/after browser navigation to complete rendered Dashboard and Reports.
- [x] Review public diff and publish a focused upstream PR (#2659).
