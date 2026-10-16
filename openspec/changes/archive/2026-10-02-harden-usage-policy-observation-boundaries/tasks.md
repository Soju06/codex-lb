## 1. Regression and implementation

- [x] 1.1 Reproduce concurrent policy overwrite on reset and quota-planner warmups and post-authorization settings reads on quota-planner warmup.
- [x] 1.2 Keep warmup authorization projections transient and reuse the resolved settings snapshot.
- [x] 1.3 Preserve routing invalidation when a committed refresh is cancelled during the policy read.
- [x] 1.4 Remove unused feature leftovers without changing public contracts.

## 2. Verification and delivery

- [x] 2.1 Run focused regressions, related policy/routing/warmup suites, backend lint and typing.
- [x] 2.2 Complete independent review of remaining frontend/backend contracts and test coverage.
- [x] 2.3 Sync normative requirements and stable context, strictly validate, verify and archive.
- [x] 2.4 Commit the cohesive reviewed fixes locally and report verification and limits.
