## 1. Regression Coverage

- [x] 1.1 Add a deterministic real-WebSocket-finalizer regression proving a failed primary settlement blocks health until fallback release commits.
- [x] 1.2 Prove an unconfirmed fallback leaves health unapplied while reconnect and retirement remain enabled.
- [x] 1.3 Cover the existing retry consumer so an unconfirmed ordering-sensitive settlement drops deferred health penalties.
- [x] 1.4 Prove shutdown drain remains blocked while an ordering-sensitive fallback release is pending, including cancellation before primary startup or during fallback.

## 2. Settlement Ordering

- [x] 2.1 Make ordering-sensitive stream settlement observe the primary task result and synchronously own fallback release without double release.
- [x] 2.2 Return confirmed settlement state and gate WebSocket health persistence on that state without changing ordinary detached settlement.
- [x] 2.3 Gate retry health persistence on confirmed settlement and avoid re-settling an already transferred reservation.
- [x] 2.4 Keep primary settlement and ordering-sensitive fallback under one tracked persistence task.

## 3. Verification

- [x] 3.1 Run focused settlement and WebSocket-finalizer regression tests.
- [x] 3.2 Run Sensitive persistence integration, affected lint/type checks, and strict scoped plus repository OpenSpec validation.
- [x] 3.3 Re-run focused retry coverage, Sensitive verification, and independent review after the retry-consumer repair.
- [x] 3.4 Re-run focused shutdown ownership coverage and Sensitive verification after the current-head review repair.
