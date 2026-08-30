## 1. Implementation

- [x] 1.1 Add `PROBE_MAX_OUTPUT_TOKENS = 16` next to the other probe constants
  in `app/modules/accounts/service.py`.
- [x] 1.2 Send that constant from `_send_probe_request` instead of `1`.
- [x] 1.3 Update the main `usage-refresh-policy` spec scenario and requirement
  text to require `max_output_tokens=16`.
- [x] 1.4 Record the floor and the #1895 partial cover in
  `openspec/specs/usage-refresh-policy/context.md`.

## 2. Regression coverage

- [x] 2.1 Assert the probe JSON body uses `max_output_tokens=16` in
  `tests/unit/test_accounts_service_probe.py`.

## 3. Validation

- [x] 3.1 Run the probe unit tests and `ruff` on the touched files.
- [x] 3.2 Run `openspec validate --specs` and scoped change validation.
