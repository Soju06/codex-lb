## 1. Regression

- [x] 1.1 Add native-client tests for the exact Codex HTTP/2 window and frame
  constants used by every persistent pool entry.
- [x] 1.2 Add model-discovery tests for exact ordered names and absence of a
  standalone `version` header.
- [x] 1.3 Add Responses header tests for position-preserving, case-insensitive
  singleton and account-id replacement without duplicates.

## 2. Implementation

- [x] 2.1 Replace adaptive HTTP/2 flow control with explicit measured Codex
  stream, connection, frame, and header-list values.
- [x] 2.2 Preserve native singleton/account header positions while replacing
  selected account credentials and content negotiation values.
- [x] 2.3 Align model-discovery ordered header names with direct Codex.

## 3. Verification

- [x] 3.1 Run focused Python and Rust tests, formatting, lint, type checks, and
  strict OpenSpec validation.
- [x] 3.2 Build the release helper and repeat the isolated A′/A/C TLS HTTP/2
  capture with privacy checks.
- [x] 3.3 Record the post-fix evidence and archive this verified change.
