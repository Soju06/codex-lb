## 1. Specification

- [x] 1.1 Modify continuous transcript retention to require bounded,
      resumable scheduler passes.
- [x] 1.2 Specify aggregate cleanup observability without sensitive labels.

## 2. Implementation

- [x] 2.1 Add fixed batch/count/time budgets and a typed cleanup result.
- [x] 2.2 Stop on a short batch or budget exhaustion and log aggregate result.
- [x] 2.3 Add Prometheus duration, deletion, outcome, and backlog metrics.

## 3. Verification

- [x] 3.1 Cover complete drain, count-budget stop, time-budget stop, disabled
      sticky cleanup, and cleanup failure.
- [x] 3.2 Run focused tests and changed-file Ruff checks.
- [x] 3.3 Run strict OpenSpec validation and `git diff --check`.
