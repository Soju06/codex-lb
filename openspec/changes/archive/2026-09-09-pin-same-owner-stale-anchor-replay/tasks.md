## 1. Specification

- [x] 1.1 Record the owner-bound stale-anchor replacement problem, rationale,
  scope, and non-goals.
- [x] 1.2 Require owner-pinned admission and fail-closed behavior when the
  proven owner is unavailable.

## 2. Implementation

- [x] 2.1 Disable preferred-owner fallback for the verified same-owner stale-
  anchor replacement path while leaving account-neutral replay unchanged.

## 3. Coverage

- [x] 3.1 Add a regression proving the replacement drops only the stale anchor,
  remains on the same account, and disables preferred-owner fallback.
- [x] 3.2 Cover the owner-unavailable fail-closed outcome without exercising an
  alternate account through the existing required-owner selection regression.

## 4. Verification

- [x] 4.1 Run focused bridge tests and affected unit/integration checks.
- [x] 4.2 Run Ruff, formatting, changed-file type checks, proxy architecture,
  simplicity, diff, and strict OpenSpec validation where available.
  Exact candidate validation: `pnpm --silent dlx
  @fission-ai/openspec@1.10.0 validate
  pin-same-owner-stale-anchor-replay --strict` passes. Full strict specs remain
  57/58 with the unrelated pre-existing `model-source-routing` failure.
- [x] 4.3 Review the immutable candidate and post exact-head evidence before
  requesting maintainer merge.
