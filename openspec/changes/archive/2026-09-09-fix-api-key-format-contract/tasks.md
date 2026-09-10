## 1. Specification

- [x] 1.1 Define exact creation and regeneration format scenarios.
- [x] 1.2 Define compatibility for already-issued base64url keys.

## 2. Regression

- [x] 2.1 Add exact-format coverage for service creation and regeneration.
- [x] 2.2 Add exact-format coverage through the API create/regenerate path.
- [x] 2.3 Capture the focused tests failing against the current generator.

## 3. Implementation

- [x] 3.1 Generate new API keys with `secrets.token_hex(24)`.
- [x] 3.2 Preserve hash validation and existing-key compatibility unchanged.

## 4. Verification

- [x] 4.1 Run focused API-key unit and integration tests.
- [x] 4.2 Exercise create, list, and regenerate through a live local HTTP API.
- [x] 4.3 Run changed-file format, lint, type, and strict OpenSpec checks.
- [x] 4.4 Run the repository risk-based local CI gate.
- [x] 4.5 Complete independent PR-readiness review and cleanup.
