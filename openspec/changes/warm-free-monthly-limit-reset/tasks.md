## 1. Regression Coverage

- [x] 1.1 Prove selected-account scheduler slices load monthly snapshots before
  and after refresh.
- [x] 1.2 Prove free plans select monthly usage while paid plans retain
  secondary usage.
- [x] 1.3 Prove an exhausted-to-reset monthly transition sends one warm-up and
  persists a monthly attempt.
- [x] 1.4 Prove a secondary-to-monthly transition cannot produce a false
  reset-confirmed warm-up.

## 2. Implementation

- [x] 2.1 Load monthly usage snapshots within the existing selected-account
  query scope.
- [x] 2.2 Map each account to its plan-applicable long-window entries before
  invoking limit warm-up.
- [x] 2.3 Preserve the selected usage row's canonical window on the warm-up
  attempt.
- [x] 2.4 Reject reset comparisons whose before and after samples represent
  different canonical windows.

## 3. Verification

- [x] 3.1 Run focused scheduler and limit warm-up tests.
- [x] 3.2 Run lint, formatting, type, architecture, simplicity, and diff gates.
- [x] 3.3 Validate OpenSpec strictly.
