## 1. Reproduction

- [x] 1.1 Reproduce a native Responses request failing before dispatch when only
  its refresh credential is rejected and its access token remains unexpired.

## 2. Implementation

- [x] 2.1 Add caller-local, non-forced preflight recovery using fresh persisted
  state and the existing access-token expiry helper.
- [x] 2.2 Keep forced failures, expired/unknown-expiry tokens, deactivation,
  session invalidation, and transient errors outside the recovery path.

## 3. Verification

- [x] 3.1 Cover the Responses route, shared ordinary/forced refresh callers,
  persisted credential preservation, and rejection boundaries.
- [x] 3.2 Run relevant auth/proxy tests, lint, type and strict change validation.
- [x] 3.3 Sync the verified requirement and archive the change.
