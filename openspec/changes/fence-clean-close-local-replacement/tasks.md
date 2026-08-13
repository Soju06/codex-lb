## 1. Durable replacement contract

- [x] 1.1 Advance the durable owner epoch when a fresh local session replaces
  an unrepresented durable generation owned by the current instance.
- [x] 1.2 Preserve ordinary local reuse and cross-instance ownership checks.

## 2. Regression coverage

- [x] 2.1 Add a deterministic product-path test that overlaps clean-close
  retirement with replacement creation and proves the second request succeeds.
- [x] 2.2 Add focused ownership coverage proving the detached generation's
  late release cannot clear the replacement owner.

## 3. Verification

- [x] 3.1 Run the focused HTTP bridge integration and durable-session tests
  repeatedly.
- [ ] 3.2 Run strict OpenSpec validation and the relevant lint/type/local CI
  gates.
