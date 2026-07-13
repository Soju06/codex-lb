## Implementation

- [x] Add dashboard OAuth callback error-sanitization requirement
- [x] Sanitize unexpected `manual_callback` API errors while preserving `OAuthError`
- [x] Sanitize browser callback HTML errors for account identity conflicts
- [x] Refactor CodeQL-triggering path/URL/logging patterns without weakening behavior
- [x] Add regression tests for sanitized OAuth callback errors and existing invariants
- [x] Run focused tests and OpenSpec validation
