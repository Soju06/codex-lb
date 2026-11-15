## 1. Implementation

- [x] 1.1 Canonicalize the Business Pro Lite alias consistently and recognize Pro Max with Pro model eligibility.
- [x] 1.2 Give Pro Max the existing Pro capacity estimates while preserving its plan identifier.
- [x] 1.3 Keep recognized Pro-family plans in the existing coarse telemetry bucket.

## 2. Verification

- [x] 2.1 Verify usage refresh accepts the new paid-plan identities and writes their usage.
- [x] 2.2 Verify Pro-gated selection and dashboard capacity for the recognized plans.
- [x] 2.3 Run focused regression tests, lint, typing, and strict OpenSpec validation; sync the main specs.
- [x] 2.4 Archive the verified change.
