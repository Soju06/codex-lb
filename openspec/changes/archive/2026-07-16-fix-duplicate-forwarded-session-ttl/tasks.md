## 1. Regression Evidence

- [x] 1.1 Add a raw repeated-header regression proving that a later non-empty forwarded client identity receives the 12-hour cap.

## 2. Implementation

- [x] 2.1 Inspect every occurrence of every supported forwarded client-IP header before granting the long-session loopback-host-header override.
- [x] 2.2 Preserve existing behavior for absent headers and fields whose every value is empty.

## 3. Specification Sync

- [x] 3.1 Sync the repeated-field requirement and security context into the main `admin-auth` capability.

## 4. Verification

- [x] 4.1 Run the original exploit reproduction and focused dashboard session TTL tests.
- [x] 4.2 Run diagnostics, strict OpenSpec validation, diff checks, and the full fast CI gate.
- [x] 4.3 Archive the verified OpenSpec change and revalidate the main specifications.
