## 1. Recovery invariant

- [x] 1.1 Extend the existing deterministic fresh-primary recovery test to distinguish available, exhausted, and elapsed-window samples; verify the exhausted case fails before the production change.
- [x] 1.2 Gate early reset clearing on the applicable normalized usage; verify the recovery cases and unchanged HTTP refresh/cache regression pass.

## 2. Integration and verification

- [x] 2.1 Verify the complete load-balancer and usage-recovery suites, HTTP continuation checks, lint, and type checking; inspect review results for credit, normalization, and ownership regressions.
- [x] 2.2 Validate and synchronize the owning OpenSpec requirement and context so the verified change is ready for archive.
