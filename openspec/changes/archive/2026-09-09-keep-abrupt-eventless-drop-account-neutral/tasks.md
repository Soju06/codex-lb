## 1. Implementation

- [x] 1.1 Add `_is_account_neutral_transport_drop` (no close frame AND zero
  response events) beside `_classify_upstream_close`.
- [x] 1.2 Consult it in the HTTP bridge reader failure path so the frame-less
  eventless drop no longer sets `penalize_account=True`.
- [x] 1.3 Record account-neutral drops into the windowed eventless account
  drain signal (`_record_http_bridge_account_timeout_signal`) so repeated
  drops still drain the account.

## 2. Regression coverage

- [x] 2.1 Flip the `[routed-receive-error]` pin intentionally:
  `penalize_account is False` for a frame-less eventless drop.
- [x] 2.2 Assert the drop records the windowed drain signal with
  `detail=eventless_transport_drop`.
- [x] 2.3 Assert a drop after streamed response events still penalizes.
- [x] 2.4 Assert a non-clean close frame (1008/1011) with zero events still
  penalizes.
- [x] 2.5 Assert a non-terminal protocol-invalid binary frame still penalizes
  and records no drop signal.
- [x] 2.6 Assert the synthetic abnormal-closure code 1006 counts as
  frame-less and stays account-neutral.
- [x] 2.7 Assert a drop after a buffered reasoning prelude (output observed,
  zero response events) still penalizes.
- [x] 2.8 Helper unit coverage for `_is_account_neutral_transport_drop`.

## 3. Validation

- [x] 3.1 Run the HTTP bridge unit suite and proxy utils suite.
- [x] 3.2 Run strict OpenSpec validation for this change.
