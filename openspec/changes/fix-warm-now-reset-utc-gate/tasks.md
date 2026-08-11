## 1. Warm-now reset gate

- [x] 1.1 Compare warm-now usage reset epochs against a current epoch computed
  from naive UTC rather than process-local time.
- [x] 1.2 Preserve existing no-short-window and budget safety gates.

## 2. Regression coverage

- [x] 2.1 Simulate a UTC+ process timezone in the route-level warm-now test
  with `TZ`, `time.tzset()`, and cleanup.
- [x] 2.2 Verify the strengthened test fails when the warm-now epoch comparison
  is temporarily reverted to process-local timestamp conversion.

## 3. Validation

- [x] 3.1 Validate the OpenSpec change artifacts.
- [x] 3.2 Re-run the target integration test file and one broader integration
  slice after restoring the production fix.
