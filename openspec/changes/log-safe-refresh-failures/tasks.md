## 1. Implementation
- [x] Add content-free, account-correlatable failure diagnostics.
- [x] Test permanent/transient failures and rejection of untrusted error codes.
- [x] Validate alongside revocation routing backport before deployment.
- [x] Port diagnostics alone to current main and validate auth-manager tests, lint, formatting, types, and this OpenSpec change.
- [x] Preserve known pre-exchange route/admission error codes and label warnings as refresh-attempt failures.
- [x] Exercise ordinary/private singleflight failures in both arrival orders and prove local failures make no provider call.
