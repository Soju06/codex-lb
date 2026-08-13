## 1. Implementation

- [x] 1.1 Refuse foreign `claim_session` when the row is DRAINING and the
  lease is still live.
- [x] 1.2 Align `_http_bridge_allow_durable_takeover` with the live-owner
  turn-state helper.

## 2. Regression coverage

- [x] 2.1 Assert `claim_live_session(allow_takeover=False)` after
  `mark_instance_draining` keeps the original owner.
- [x] 2.2 Assert `_http_bridge_allow_durable_takeover` is false for a live
  DRAINING lookup and true for expired or released DRAINING.

## 3. Validation

- [x] 3.1 Run the new claim and helper tests plus the existing turn-state
  fail-closed tests.
- [x] 3.2 Run strict OpenSpec validation for this change.
