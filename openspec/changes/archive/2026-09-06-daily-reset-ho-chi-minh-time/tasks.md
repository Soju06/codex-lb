## 1. Daily Schedule

- [x] 1.1 Calculate daily boundaries at midnight UTC+7 and verify fixed before/at/after-midnight unit cases and unchanged non-daily windows.
- [x] 1.2 Derive the alignment timer ten minutes before the shared daily boundary, update logs, and verify timer rollover and leader-gated scheduling tests.

## 2. Verification and Documentation

- [x] 2.1 Verify API creation, explicit reset, lazy expiry, background expiry, and alignment preserve the expected UTC timestamps and usage behavior with regression tests.
- [x] 2.2 Run focused API-key tests and Ruff; sync the normative spec and operational context, run strict OpenSpec validation, and archive only after verification.
