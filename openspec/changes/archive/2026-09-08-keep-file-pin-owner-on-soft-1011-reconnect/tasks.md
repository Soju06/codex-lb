## 1. Implementation

- [x] 1.1 Treat `file_required_preferred_account` as a required owner in
  `_reconnect_http_bridge_session`.
- [x] 1.2 Pass `require_preferred_account` from
  `_retry_http_bridge_request_on_fresh_upstream` when a live file pin is
  present.

## 2. Regression coverage

- [x] 2.1 Assert soft `1011` reconnect with a live file pin keeps the owner
  required and does not exclude it.
- [x] 2.2 Assert soft `1011` reconnect without a file pin may still skip the
  closed account.
- [x] 2.3 Update the fresh-upstream retry call-shape assertion for the new
  `require_preferred_account` argument.
- [x] 2.4 Assert soft `1011` file-pin reconnect fails closed with the
  required-owner envelope when selection cannot return the pin account.
- [x] 2.5 Assert submit-on-closed emits the required-owner envelope when
  the pin account is selected but the replacement socket cannot be opened.

## 3. Validation

- [x] 3.1 Run the focused HTTP-bridge reconnect unit tests.
- [x] 3.2 Run strict OpenSpec validation for this change.
