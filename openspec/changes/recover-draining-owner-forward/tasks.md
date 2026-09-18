## 1. Drain Recovery

- [x] 1.1 Expose pre-dispatch owner-forward outcomes (not dispatched / receiver rejected) to the recovery gate.
- [x] 1.2 Allow bootstrap rebind for `bridge_drain_active` only on pre-dispatch rejection; turn-state keys need a session/thread fallback.
- [x] 1.3 Keep previous-response continuations out of the bootstrap rebind path.

## 2. Validation

- [x] 2.1 Add regression coverage for drain rejection classification and rebind bounding.
- [x] 2.2 Validate the OpenSpec delta strictly.
