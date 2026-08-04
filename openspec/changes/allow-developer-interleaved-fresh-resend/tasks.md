## 1. Replay classification

- [x] 1.1 Allow a valid historical developer message only in exact manifest proof.
- [x] 1.2 Keep the historical call/output match and fresh suffix checks fail-closed.
- [x] 1.3 Allow a terminal fresh developer message only after `final_answer` and exactly one explicit user follow-up.
- [x] 1.4 Allow fresh tool interleaving only for an exact custom call/developer/matching-output suffix.
- [x] 1.5 Reject malformed, account-scoped, parallel, function/apply-patch, leading, trailing, and repeated variants.
- [x] 1.6 Preserve projection-omitted developer-role items until fail-closed validation completes.
- [x] 1.7 Reject stored-prefix developer messages outside a verified pending call/output interleave.
- [x] 1.8 Preserve only the canonical account-neutral developer instruction adjacent to a Lite tool bundle.
- [x] 1.9 Prove the canonical position against the original input: the stored prefix must begin with a valid
      bundle and the developer message must be its original immediate successor.
- [x] 1.10 Bound historical interleaving to a pending window that holds exactly one outstanding call and
      consumes at most one developer message, and reject parallel and duplicate shapes.

## 2. Regression coverage

- [x] 2.1 Add focused positive and negative replay-safety cases.
- [x] 2.2 Exercise historical developer interleaving through `/v1/responses`.
- [x] 2.3 Exercise both bounded fresh developer suffixes through bridge-unit and `/v1/responses` coverage.
- [x] 2.4 Verify the new positive regressions fail before the production fix and pass after it.
- [x] 2.5 Add manifest, retained-output, and owner-failover regressions for the review findings.
- [x] 2.6 Cover canonical Lite-prefix acceptance plus nonadjacent and response-owned rejections.
- [x] 2.7 Reject a bundle that does not start the stored prefix and a developer message made adjacent only by
      projection, at both the classifier and `/v1/responses` paths.
- [x] 2.8 Classify historical-interleave fixtures through the production ID-preserving projection, and cover
      the response-owned rejection at the historical position.
- [x] 2.9 Reject parallel-batch and duplicate historical developer interleaves while keeping sequential
      single-call windows accepted.

## 3. Validation

- [x] 3.1 Run the full replay-safety, bridge-unit, and HTTP bridge integration suites.
- [x] 3.2 Run changed-file Ruff, type, diff, and strict OpenSpec checks.
