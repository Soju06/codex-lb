## 1. Consent Policy

- [x] 1.1 Resolve default undecided telemetry consent as inactive while preserving explicit environment and persisted decisions
- [x] 1.2 Update the undecided startup notice to describe default-disabled opt-in behavior

## 2. Regression Coverage

- [x] 2.1 Update consent precedence tests for the inactive default
- [x] 2.2 Verify a default scheduler startup makes zero sender calls and explicit opt-in still sends

## 3. Documentation and Validation

- [x] 3.1 Update telemetry context and rendered user documentation with the opt-in default and silent environment behavior
- [x] 3.2 Run focused tests and strict OpenSpec validation for the change
