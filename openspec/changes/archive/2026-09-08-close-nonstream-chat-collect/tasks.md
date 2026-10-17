## 1. Implementation

- [x] 1.1 Close the original `stream_responses` generator after non-stream
  chat collect, including `__anext__` errors and early collect return.
- [x] 1.2 Map collected chat error envelopes with `_status_for_error` /
  `_mask_previous_response_not_found_error`.

## 2. Regression coverage

- [x] 2.1 Assert first-event `response.failed` closes the upstream generator.
- [x] 2.2 Assert non-stream chat `rate_limit_exceeded` returns 429 and
  releases the API-key reservation.

## 3. Validation

- [x] 3.1 Run the new chat collect regressions and existing chat completion
  suites.
- [x] 3.2 Run strict OpenSpec validation for this change.
