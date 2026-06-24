## 1. Implementation

- [x] 1.1 Skip the capacity-wait recovery path for error messages that
  match the locally-generated `Rate limit exceeded. Try again in <N>s`
  shape produced by `_format_retry_hint` in
  `app/core/balancer/logic.py`.
- [x] 1.2 Keep the existing permanent-failure allowlist
  (`require re-authentication`, `all accounts are paused`,
  `no accounts with a plan`, `no accounts with available additional
  quota`, `no fresh additional quota data`) untouched.
- [x] 1.3 Keep upstream-derived recoverable messages (workspace spend cap,
  external `try again in` hints not produced by `select_account`) on the
  existing recovery path.

## 2. Tests

- [x] 2.1 Unit: `_account_selection_recovery_sleep_seconds` returns
  `None` for the maximum-capped `_format_retry_hint(300.0)` message
  shape.
- [x] 2.2 Unit: `_account_selection_recovery_sleep_seconds` returns
  `None` for a sub-cap `_format_retry_hint(30.0)` message shape, so the
  match is not tied to the 300s ceiling.
- [x] 2.3 Existing parametrized permanent-failure cases continue to
  return `None`.

## 3. Spec Delta

- [x] 3.1 Add `responses-api-compat` requirement covering locally-generated
  selector retry hints in the capacity-wait recovery path with
  single-account and multi-account scenarios.
- [x] 3.2 Validate the OpenSpec change with
  `openspec validate fix-single-account-rate-limit-hang --strict` once
  the local toolchain is available.
