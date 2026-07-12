## 1. Codex Usage Reset Credits

- [x] 1.1 Extend usage payload schemas to carry reset-credit availability.
- [x] 1.2 Add an upstream client helper for consuming a reset credit.
- [x] 1.3 Expose reset-credit availability on `/api/codex/usage`.
- [x] 1.4 Add `/api/codex/rate-limit-reset-credits/consume` with ChatGPT caller auth.
- [x] 1.5 Force-refresh matching account usage after successful/idempotent consume.

## 2. Verification

- [x] 2.1 Add integration coverage for availability and consume flows.
- [x] 2.2 Run focused backend tests.
- [x] 2.3 Run OpenSpec validation when the CLI is available.

## 3. Dashboard Account Detail

- [x] 3.1 Add dashboard account reset-credit availability contract and endpoint.
- [x] 3.2 Render reset-credit availability on the selected account usage panel.
- [x] 3.3 Add focused dashboard backend/frontend coverage and rebuild assets.
- [x] 3.4 Add a confirmed Usage panel Reset action that consumes a reset credit and uses usage-only force refresh.
- [x] 3.5 Ensure probe/consume-triggered usage refresh bypasses scheduler-disabled mode.
