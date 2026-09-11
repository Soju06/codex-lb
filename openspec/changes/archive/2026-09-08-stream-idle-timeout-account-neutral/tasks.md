## 1. Implementation

- [x] 1.1 Classify `stream_idle_timeout` as account-neutral in
  `_is_account_neutral_error_code`.
- [x] 1.2 Keep this-request exclude/failover for the idle account.

## 2. Regression coverage

- [x] 2.1 Assert `_handle_stream_error` does not call `record_error` for
  `stream_idle_timeout`.
- [x] 2.2 Assert the existing first-event idle-timeout failover still succeeds
  and the idle account's error_count stays 0.

## 3. Validation

- [x] 3.1 Run the new helper regression and the existing idle-timeout failover
  integration test.
- [x] 3.2 Run strict OpenSpec validation for this change.
