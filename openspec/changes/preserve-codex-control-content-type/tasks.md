## 1. Contract

- [x] 1.1 Record the native control-header failure and media-type contract.

## 2. Implementation

- [x] 2.1 Reproduce native search duplicate media-type headers at the transport boundary.
- [x] 2.2 Replace the control request media type case-insensitively.
- [x] 2.3 Cover public search aliases, native and SDK casing, SDP, and bodyless requests.

## 3. Verification and delivery

- [x] 3.1 Run focused regression tests and lint.
- [x] 3.2 Validate the change with `openspec validate --strict`.
- [ ] 3.3 Sync the delta into `openspec/specs/responses-api-compat/` and archive after merge.
