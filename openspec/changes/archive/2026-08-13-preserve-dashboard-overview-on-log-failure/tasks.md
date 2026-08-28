## 1. Regression Coverage

- [x] 1.1 Port the strict MSW-backed `/dashboard` partial-failure reproduction into the permanent integration suite and record its pre-fix failure.
- [x] 1.2 Extend the regression through 500-to-200 Retry recovery, asserting only request logs refetch and healthy overview content never disappears.

## 2. Dashboard Isolation

- [x] 2.1 Gate overview-backed composition only on overview data and render Request Logs initial-loading, terminal-error, and ready states inside its section.
- [x] 2.2 Wire the scoped Retry action to the existing request-log query refetch operation while preserving all-success and overview-loading behavior.

## 3. Verification

- [x] 3.1 Run focused regression tests, relevant frontend unit/integration coverage, typecheck, lint, build, and the prescribed frontend suite.
- [x] 3.2 Complete desktop browser smoke verification with identical request-log interception and capture before/after evidence outside the repository.
- [x] 3.3 Promote stable frontend-architecture context, run strict OpenSpec validation, and verify the completed change against its artifacts.
