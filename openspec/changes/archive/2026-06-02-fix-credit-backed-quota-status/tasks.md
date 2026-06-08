## 1. Quota Status

- [x] 1.1 Treat exhausted secondary-window usage as usable when credit fields show usable capacity.
- [x] 1.2 Use the same credit-aware quota interpretation in proxy account state construction and account-summary status mapping.
- [x] 1.3 Preserve primary-window `rate_limited` precedence and paused/deactivated state preservation.

## 2. Tests and Validation

- [x] 2.1 Add regression coverage for proxy selection with secondary credits.
- [x] 2.2 Add regression coverage for account-summary effective status with secondary credits.
- [x] 2.3 Run focused backend tests and OpenSpec validation.
