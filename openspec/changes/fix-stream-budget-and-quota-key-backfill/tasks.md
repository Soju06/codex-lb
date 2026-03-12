## 1. Stream budget enforcement

- [x] 1.1 Reapply remaining-budget timeout overrides for stream connect, idle, and total timeouts on the initial stream attempt
- [x] 1.2 Reapply remaining-budget timeout overrides for the forced-refresh retry stream attempt
- [x] 1.3 Add regression coverage for stream timeout override propagation

## 2. Registry-backed migration backfill

- [x] 2.1 Backfill `additional_usage_history.quota_key` through the configured registry canonicalization helper
- [x] 2.2 Add migration coverage for a deployment-specific registry override

## 3. Validation

- [x] 3.1 Validate OpenSpec artifacts
- [x] 3.2 Run targeted unit test coverage for proxy timeout and migration regressions
- [ ] 3.3 Review diffs, commit, and push branch updates
