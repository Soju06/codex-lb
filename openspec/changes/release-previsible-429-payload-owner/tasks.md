## 1. Specification

- [x] 1.1 Define pre-visible rate-limit and quota rejection as
      non-owner-establishing for a pending transient dispatch owner
- [x] 1.2 Preserve independently established hard account ownership
- [x] 1.3 Reconcile the existing dispatch-owner requirement with the narrow
      classified pre-visible rejection exception

## 2. Implementation

- [x] 2.1 Exempt classified pre-visible HTTP 429 and first-event failures from
      transient payload-owner registration

## 3. Tests

- [x] 3.1 Add a routed compacted-input regression that fails over from a
      limited account to another eligible account
- [x] 3.2 Preserve the account-neutral HTTP 429 regression as a separate control
- [x] 3.3 Add deterministic first-event limit regression coverage
- [x] 3.4 Run focused retry tests and relevant hard-owner controls
- [x] 3.5 Add routed cross-account encrypted-reasoning coverage that asserts
      ciphertext is forwarded unchanged

## 4. Validation

- [x] 4.1 Run Ruff checks and formatting verification for changed Python files
- [x] 4.2 Run strict OpenSpec validation
- [x] 4.3 Build and smoke-test a container against a disposable database copy
