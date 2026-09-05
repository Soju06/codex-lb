## 1. Specification

- [x] 1.1 Define pre-visible HTTP 429 as non-owner-establishing for a pending
      transient dispatch owner
- [x] 1.2 Preserve independently established hard account ownership

## 2. Implementation

- [x] 2.1 Exempt pre-visible HTTP 429 from transient payload-owner registration

## 3. Tests

- [x] 3.1 Add a routed compacted-input regression that fails over from a
      limited account to another eligible account
- [x] 3.2 Run focused retry tests and relevant hard-owner controls

## 4. Validation

- [x] 4.1 Run Ruff checks and formatting verification for changed Python files
- [x] 4.2 Run strict OpenSpec validation
- [x] 4.3 Build and smoke-test a container against a disposable database copy
