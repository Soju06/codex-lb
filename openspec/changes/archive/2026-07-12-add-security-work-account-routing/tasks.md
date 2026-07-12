## 1. Specification

- [x] Add Responses API compatibility requirements for security-work account routing and advisory warnings.
- [x] Add frontend requirements for account-level Trusted Access for Cyber controls.

## 2. Implementation

- [x] Add `accounts.security_work_authorized` schema/model/API wiring and migration.
- [x] Route eligible security-work authorization failures to authorized accounts.
- [x] Preserve file and previous-response account affinity when a retry would be unsafe.
- [x] Surface non-terminal `codex_lb.warning` events for retry and missing-authorized-pool cases.
- [x] Add account dashboard controls for the security-work authorization flag.

## 3. Verification

- [x] Run focused backend proxy/load-balancer/account tests.
- [x] Run focused frontend account-page tests.
- [x] Run OpenSpec validation.
