## 1. Stream budget enforcement

- [x] 1.1 Reapply remaining-budget timeout overrides for stream connect, idle, and total timeouts on the initial stream attempt
- [x] 1.2 Reapply remaining-budget timeout overrides for the forced-refresh retry stream attempt
- [x] 1.3 Add regression coverage for stream timeout override propagation

## 2. Registry-backed migration backfill

- [x] 2.1 Normalize configured additional quota keys before storing runtime definitions
- [x] 2.2 Backfill `additional_usage_history.quota_key` through the configured registry mapping available at upgrade time
- [x] 2.3 Keep repository queries/deletes compatible with raw aliases after canonical key renames
- [x] 2.4 Add regression coverage for normalized runtime keys, configured backfill, and compatibility reads

## 3. Canonical alias refresh coalescing

- [x] 3.1 Merge additional-usage aliases by canonical `quota_key` before pruning persisted rows
- [x] 3.2 Add regression coverage for mixed-alias null/data payloads and split-window alias payloads

## 4. Validation

- [x] 4.1 Validate OpenSpec artifacts
- [x] 4.2 Run targeted unit test coverage for proxy timeout, migration, and usage refresh regressions
- [x] 4.3 Review diffs, commit, and push branch updates

## 5. Legacy quota-key compatibility

- [x] 5.1 Add registry/runtime support for legacy `quota_key` aliases so renamed canonical keys can still read/delete existing rows.
- [x] 5.2 Add regression coverage for legacy `quota_key` aliases across repository list/read/delete paths.
