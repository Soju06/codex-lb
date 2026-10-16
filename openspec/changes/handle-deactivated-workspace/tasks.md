## 1. Implementation

- [x] 1.1 Recognize `deactivated_workspace` in the existing permanent-failure policy.
- [x] 1.2 Add account-unavailable classification and bounded deterministic failover while retaining ownership/visibility guards.

## 2. Verification

- [x] 2.1 Prove the existing defect with failing classifier and real-route regression tests.
- [x] 2.2 Verify workspace-scoped exclusion, healthy-candidate success, owner-bound fail-closed behavior, and bare-402 safety.
- [x] 2.3 Run focused tests, available repository lint/type/build gates, and strict OpenSpec validation; record broader CI limitations in the PR.
