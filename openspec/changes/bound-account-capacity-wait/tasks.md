# Tasks

## 1. Bounded account-capacity wait

- [x] 1.1 Add a fixed 120-second account-capacity wait ceiling without adding a setting.
- [x] 1.2 Clamp account-capacity waits while leaving `response_create_gate_timeout` waits budget-bounded.
- [x] 1.3 Preserve the first local-cap deadline and error across recovery and request-state preparation.
- [x] 1.4 Surface the original HTTP 429 cap envelope when the ceiling expires.
- [x] 1.5 Record terminal capacity overloads in request logs before raising.
- [x] 1.6 Re-check the effective deadline after bounded sleeps before retrying.

## 2. Tests

- [x] 2.1 Cover deadline clamping, preservation, terminal error propagation, gate-wait exemption, and request-log visibility.
