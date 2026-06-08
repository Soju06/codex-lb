## 1. Implementation

- [x] 1.1 Add persisted dashboard settings for primary and secondary sticky reallocation thresholds.
- [x] 1.2 Expose the split thresholds through the settings API and dashboard payload.
- [x] 1.3 Thread the secondary threshold into sticky-session reallocation selection.
- [x] 1.4 Keep non-sticky fresh selection from applying the sticky secondary threshold.

## 2. Verification

- [x] 2.1 Add settings API/schema/migration regression coverage.
- [x] 2.2 Add load-balancer coverage for sticky secondary pressure and non-sticky fresh selection.
- [x] 2.3 Run focused backend and frontend settings tests.
