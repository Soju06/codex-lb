## 1. Native terminal carrier

- [x] 1.1 Add the shared native give-up code, default delay, and message builder
  with the canonical retry hint before upstream text.
- [x] 1.2 Preserve the synthetic transport marker through retry exhaustion for
  native streams that already committed a response.
- [x] 1.3 Translate marked transport exhaustion and equivalent native stream
  exceptions into one public retryable `response.failed` event.
- [x] 1.4 Keep local pre-dispatch refusals, unmarked raw EOF, non-native shaping,
  and internal account-health/request-log error codes unchanged.

## 2. Regression coverage

- [x] 2.1 Cover marked transport and incomplete-terminal conversion.
- [x] 2.2 Cover direct native stream exceptions, default and explicit delays,
  and an upstream message containing a competing retry hint.
- [x] 2.3 Cover the native retry-layer committed-stream path and assert the
  original internal marker/code before public normalization.
- [x] 2.4 Cover local refusal and non-give-up negative controls.
- [x] 2.5 Cover closure of the marked-terminal stream chain and account-lease
  cleanup handoff.

## 3. Validation

- [x] 3.1 Run focused native-stream regressions.
- [x] 3.2 Run `tests/unit/test_proxy_utils.py`.
- [x] 3.3 Run Responses contract, bridge, streaming-retry, timeout-hardening,
  and transient-retry suites.
- [ ] 3.4 Run current-head CI after publication.
- [x] 3.5 Run strict OpenSpec validation for the change and all 66 current specs.
- [ ] 3.6 Deploy through the overlay-preserving live helper and replay a native
  `/backend-api/codex/responses` operation with exact response/request-log
  readback.
