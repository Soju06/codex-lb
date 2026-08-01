## 1. Replay classification

- [x] 1.1 Allow a valid historical developer message only in exact manifest proof.
- [x] 1.2 Keep the historical call/output match and fresh suffix checks fail-closed.
- [x] 1.3 Keep the alternative retained-output proof unchanged.

## 2. Regression coverage

- [x] 2.1 Add focused positive and negative replay-safety cases.
- [x] 2.2 Exercise the developer-interleaved resend through `/v1/responses`.
- [x] 2.3 Verify the route regression fails before the production fix and passes after it.

## 3. Validation

- [x] 3.1 Run focused replay-safety and HTTP bridge tests plus changed-file Ruff checks.
- [x] 3.2 Run strict OpenSpec validation.
