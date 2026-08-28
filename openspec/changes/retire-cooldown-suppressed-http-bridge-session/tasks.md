# Tasks

## Specification

- [x] Add the `responses-api-compat` delta for retiring sessions rejected by
      hard-key retry-circuit cooldown before upstream dispatch.
- [x] Record the late-submit and startup pre-submit scenarios, including the
      replay bypass and shared-pending-work safeguards.

## Implementation

- [x] Mark and retire the session in the late submit suppression branch.
- [x] Mark and retire the session in the startup continuity cooldown terminal
      branch.
- [x] Keep proof-gated and operation-fenced bypasses unchanged.

## Regression coverage

- [x] Assert late suppression returns the same 503, marks the session retiring,
      invokes the bounded retire helper, and never sends upstream.
- [x] Assert startup pre-submit suppression marks the session retiring, invokes
      the helper, preserves the 503 envelope, and never submits.
- [x] Assert the replay bypass path does not retire the session.

## Verification

- [x] Run focused and full HTTP bridge unit/integration tests and Ruff on
      changed files.
- [x] Run strict OpenSpec validation and inspect the final diff/status.

Validation note: the change passes strict OpenSpec validation. The repository's
non-strict full spec scan reports one pre-existing `model-source-routing`
failure; the affected `responses-api-compat` and `proxy-admission-control`
specs pass.
