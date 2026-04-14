## 1. Specs

- [x] 1.1 Add `responses-api-compat` requirements for downstream websocket idle expiry.
- [x] 1.2 Validate OpenSpec changes.

## 2. Tests

- [x] 2.1 Add websocket integration coverage for idle downstream-session expiry.
- [x] 2.2 Add settings coverage for the downstream websocket idle-timeout default and env override.

## 3. Implementation

- [x] 3.1 Add a configurable downstream websocket idle-timeout setting.
- [x] 3.2 Expire idle downstream Responses websockets only when no requests are pending, and ensure normal cleanup releases the upstream socket and lane capacity.
