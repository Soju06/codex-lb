## 1. Regression

- [x] 1.1 Reproduce a later routed file-finalize poll replay through the actual HTTP route with an unpinned file.
- [x] 1.2 Verify pinned and first-poll pre-dispatch controls without timing sleeps.

## 2. Implementation

- [x] 2.1 Deny cross-account replay when an earlier routed poll returned an upstream response.
- [x] 2.2 Keep the owning spec and context synchronized with the verified contract.

## 3. Verification

- [x] 3.1 Pass focused route/client/unary tests, diagnostics, and strict OpenSpec validation.
- [x] 3.2 Prove first-poll fallback and later-poll fail-closed behavior through live HTTP, and clean owned resources.
- [x] 3.3 Pass the serial full Linux gate before publication and archive; record the exact tested commit and verification evidence.
