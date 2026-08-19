## 1. Detection

- [x] 1.1 Match a terse upstream rejection that names `previous_response_id` alongside "invalid", in addition to the existing "previous response … not found" wording
- [x] 1.2 Treat an absent error `param` as inconclusive; only a different `param` disqualifies the anchor as the cause

## 2. Tests

- [x] 2.1 The terse shape (`code=invalid_request_error`, no `param`, message ``Invalid `previous_response_id`.``) is classified as a previous-response miss
- [x] 2.2 The pre-existing shapes (canonical code, and `param=previous_response_id` with "not found") still classify
- [x] 2.3 A different `param`, an unrelated `invalid_request_error` message, and a non-400 code that merely mentions the anchor are all left unmatched
