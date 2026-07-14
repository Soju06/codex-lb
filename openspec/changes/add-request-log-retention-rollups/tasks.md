## Tasks

- [x] 1. Add normative retention and aggregation requirements.
- [x] 2. Add request-log daily aggregate storage and migration.
- [x] 3. Implement dry-run/apply retention service with a raw retention floor.
- [x] 4. Expose an operator CLI command for retention dry-runs and applies.
- [x] 5. Add regression tests for aggregation-before-prune behavior and defaults.
- [ ] 6. Run OpenSpec and test validation.
  - [x] 6.1 Run lint, focused unit, CLI, and migration validation.
  - [ ] 6.2 Run OpenSpec validation. Blocked locally because the `openspec` executable is not installed.
- [ ] 7. Configure and verify seven-day retention.
  - [x] 7.1 Set the runtime default and minimum raw retention window to seven days.
  - [x] 7.2 Add a fail-closed row-count parity check before committing a prune.
  - [x] 7.3 Add regression coverage for the seven-day boundary, aggregate totals, and parity rollback.
  - [x] 7.4 Preserve API-key fallback, account dedupe, dashboard, report user-agent, and limit projections.
  - [ ] 7.5 Validate locally, deploy through Railway, and prove live raw-plus-aggregate totals are preserved.
