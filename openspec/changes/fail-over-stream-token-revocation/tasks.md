## 1. Contract and regression

- [x] 1.1 Define pre-visible `token_revoked` failover and revoked-account
  routing exclusion.
- [x] 1.2 Add a two-account product-path regression with sticky affinity,
  encrypted reasoning state, and the canonical terminal event shape.
- [x] 1.3 Preserve hard-owner and refresh-token-only routing controls.

## 2. Implementation

- [x] 2.1 Classify and persist `token_revoked` as `reauth_required`.
- [x] 2.2 Remove access-token-revoked accounts from current and future routing.
- [x] 2.3 Reproject an otherwise unanchored pre-visible request through the
  shared account-neutral replay gate before cross-account retry.
- [x] 2.4 Preserve the original auth error when replay proof or a replacement
  account is unavailable.

## 3. Verification and handoff

- [x] 3.1 Run focused unit and Responses integration coverage.
- [x] 3.2 Run changed-file Ruff, formatting, type checks, architecture checks,
  and strict OpenSpec validation.
- [x] 3.3 Open the linked upstream PR and verify required GitHub checks.
- [x] 3.4 Deploy atomically with the existing data volume and verify live
  routing no longer selects known revoked access tokens.
