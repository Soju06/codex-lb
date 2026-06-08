## 1. Implementation

- [x] 1.1 Add the persisted `prefer_earlier_reset_window` dashboard setting.
- [x] 1.2 Expose and validate the setting through the settings API and import payload.
- [x] 1.3 Thread the setting through proxy account selection surfaces.
- [x] 1.4 Add dashboard controls and status display for the setting.

## 2. Verification

- [x] 2.1 Add balancer coverage for primary and secondary reset-window ordering.
- [x] 2.2 Add proxy/bridge/WebSocket coverage proving the setting is passed through.
- [x] 2.3 Add settings API, schema, import, migration, and frontend coverage.
- [x] 2.4 Run focused backend and frontend checks.
- [x] 2.5 Run OpenSpec validation.
