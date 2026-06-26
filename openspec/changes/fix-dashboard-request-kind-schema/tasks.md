## 1. Request-log schema resilience

- [x] 1.1 Change the frontend request-log schema to accept string request kinds instead of a closed enum.
- [x] 1.2 Preserve the existing default of `normal` for omitted request-kind fields.
- [x] 1.3 Add display labels for known non-normal backend kinds while falling back to the raw value for future kinds.

## 2. Regression coverage and validation

- [x] 2.1 Add frontend schema coverage for `prewarm` and `compaction` request kinds.
- [x] 2.2 Validate captured live production dashboard payloads against the frontend schemas.
- [x] 2.3 Run focused frontend tests and typecheck.
