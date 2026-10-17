## 1. Contract and regression

- [x] 1.1 Document the role-independent password-session lifetime and retained limits.
- [x] 1.2 Reproduce the remote admin mismatch through the password-login HTTP route.

## 2. Implementation

- [x] 2.1 Use the existing lifetime resolver for password login and TOTP completion.
- [x] 2.2 Update role-specific unit coverage and retain local/proxy boundary coverage.

## 3. Verification and delivery

- [x] 3.1 Run focused authentication tests, diagnostics, lint, and strict OpenSpec validation.
- [x] 3.2 Build the package and verify remote login cookies through a real HTTP runtime.
- [x] 3.3 Sync the verified requirements and context, then archive this change.
- [x] 3.4 Review the focused public PR diff and record verification limitations.
