## 1. Implementation

- [x] 1.1 Wrap the reservation lifetime in `_build_models_response` with
  `try/finally`.
- [x] 1.2 Wrap the reservation lifetime in `_build_codex_models_response` with
  `try/finally`.

## 2. Regression coverage

- [x] 2.1 Assert both model catalog builders release after a catalog lookup
  exception, including the real reservation row where available.
- [x] 2.2 Preserve success-path release coverage.

## 3. Validation

- [x] 3.1 Run the model leak regression and named model/reservation suites.
- [x] 3.2 Run strict OpenSpec validation.
