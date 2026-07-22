## 1. Scheduler Scope

- [x] 1.1 Select the account before loading usage snapshots and filter before/after latest-usage queries to that account id.
- [x] 1.2 Reload post-refresh account state while passing only the selected current account to warm-up and recoverable-status evaluation.
- [x] 1.3 Filter recovery primary, secondary, and monthly lookups at the repository boundary to recoverable candidate ids.

## 2. Warm-up Cohort Preservation

- [x] 2.1 Separate `LimitWarmupService` candidate accounts from the optional full-roster stagger cohort without changing default callers.
- [x] 2.2 Prove staggered phase calculation retains all eligible ids while attempts and sends remain selected-account-only.

## 3. Regression Coverage

- [x] 3.1 Add scheduler unit regressions for selected-account query scope, ownership, failure isolation, rotation, and closed-session network handoff.
- [x] 3.2 Add a repository-backed scheduler integration test proving unrelated usage rows and accounts never enter the selected slice.

## 4. Verification

- [x] 4.1 Run focused scheduler, recovery, warm-up, updater, and usage-repository tests.
- [x] 4.2 Run the proportional full unit/integration baseline plus Ruff, formatting, type checking, architecture, simplicity, and diff checks.
- [x] 4.3 Validate OpenSpec strictly and perform semantic verification against every requirement and scenario.
