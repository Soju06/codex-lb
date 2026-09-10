## 1. Specification

- [x] 1.1 Define the compact upstream header sanitation contract and preserve
      native identity, authorization, account, and continuity behavior.

## 2. Implementation

- [x] 2.1 Sanitize fixed and `Connection`-nominated hop by hop headers in the
      shared rebuilt HTTP header path without dropping ordinary `Accept` or
      `Content-Type` negotiation.

## 3. Regression coverage

- [x] 3.1 Add header-builder coverage for transfer encoding, connection tokens,
      native identity/auth/account headers, continuity, and the current upstream
      routing-hint behavior.
- [x] 3.2 Add route-level compact coverage that exercises the real compact route
      with a chunked inbound header and verifies the rebuilt upstream headers.

## 4. Validation

- [x] 4.1 Re-run the route regression on the current upstream baseline and the
      rebased candidate to confirm baseline failure and candidate success.
- [x] 4.2 Run focused compact/header tests, Ruff, and strict OpenSpec
      validation on the rebased candidate.

Validation on current main 43a45f78: the route regression fails without sanitation (forwarded `transfer-encoding`), and all six focused header/route tests pass with it. Ruff, formatting, contributor coverage, and strict change validation pass.
