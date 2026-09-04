## 1. Daily Boundary Calculation

- [x] 1.1 Align fresh and advanced daily reset timestamps to the next 00:00 UTC boundary and verify deterministic limit-window unit tests pass.

## 2. Scheduled Alignment Job

- [x] 2.1 Add a repository operation that updates only non-aligned daily reset timestamps while preserving counters, verified on the integration database.
- [x] 2.2 Add the cancellable 23:50 UTC wall-clock loop to the existing leader-gated API-key limit scheduler, verified by unit tests for delay calculation, leader gating, and repository calls.

## 3. Validation

- [x] 3.1 Run focused API-key scheduler, limit-window, repository, and API integration tests plus Ruff on changed Python files.
- [x] 3.2 Run strict OpenSpec validation for the completed change artifacts.
