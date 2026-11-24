## 1. Dashboard

- [x] 1.1 Derive the redeemable accounts (banked credit, status not paused, reauth-required or deactivated) from the account summaries.
- [x] 1.2 Add the **Redeem all resets** action and a confirmation dialog that lists accounts and credit counts.
- [x] 1.3 Redeem sequentially through the per-account consume endpoint with a distinct `redeemRequestId` per account, collecting per-account results.
- [x] 1.4 Show per-account outcomes, keep the dialog open on any failure, and add en/ko/zh-CN strings.

## 2. Verification

- [x] 2.1 Unit tests for target selection, the dialog (confirmation list, all-success, partial failure) and the list action visibility.
- [x] 2.2 `openspec validate --specs`.
