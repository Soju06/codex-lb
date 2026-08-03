## 1. Replay classification

- [x] 1.1 Allow a valid historical developer message only in exact manifest proof.
- [x] 1.2 Keep the historical call/output match and fresh suffix checks fail-closed.
- [x] 1.3 Allow a terminal fresh developer message only after `final_answer` and exactly one explicit user follow-up.
- [x] 1.4 Allow fresh tool interleaving only for an exact custom call/developer/matching-output suffix.
- [x] 1.5 Reject malformed, account-scoped, parallel, function/apply-patch, leading, trailing, and repeated variants.

## 2. Regression coverage

- [x] 2.1 Add focused positive and negative replay-safety cases.
- [x] 2.2 Exercise historical developer interleaving through `/v1/responses`.
- [x] 2.3 Exercise both bounded fresh developer suffixes through bridge-unit and `/v1/responses` coverage.
- [x] 2.4 Verify the new positive regressions fail before the production fix and pass after it.

## 3. Validation

- [x] 3.1 Run the full replay-safety, bridge-unit, and HTTP bridge integration suites.
- [x] 3.2 Run changed-file Ruff, type, diff, and strict OpenSpec checks.
