## 1. Proxy quarantine

- [x] 1.1 Exclude every `reauth_required` account from load-balancer candidate selection and all-reauth pool reporting.
- [x] 1.2 Reject live HTTP bridge reuse when its account is `reauth_required`.

## 2. Regression coverage

- [x] 2.1 Update selector tests to prove an unexpired `reauth_required` account is excluded and active fallback wins.
- [x] 2.2 Update HTTP bridge tests to prove warning-state sessions are not reusable.
- [x] 2.3 Run focused tests, lint, and strict OpenSpec validation.
