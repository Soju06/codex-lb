## 1. Contract and regression

- [x] 1.1 Define failover for permanent authentication failure discovered by
  compact forced refresh.
- [x] 1.2 Add a failing two-account product-path regression matching remote
  compact with a revoked selected account.
- [x] 1.3 Preserve a pinned-request control that cannot cross accounts.

## 2. Implementation

- [x] 2.1 Mark and exclude the permanently failed account before continuing
  bounded compact selection.
- [x] 2.2 Preserve terminal settlement and the original error when failover is
  unsafe or unavailable.

## 3. Verification

- [x] 3.1 Run focused compact tests and the related proxy suite.
- [x] 3.2 Run changed-file lint, formatting, type, architecture, and strict
  OpenSpec validation.
- [x] 3.3 Exercise the live remote-compact task after an atomic deployment with
  a pre-cutover data backup and rollback container.
