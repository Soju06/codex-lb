# Tasks

- [x] Specify that upstream rejections of the request payload are account neutral.
- [x] Add the narrow request-rejection predicate and gate `_handle_stream_error` on it, with an observable skip log.
- [x] Keep account-scoped `invalid_request_error` rejections penalizing.
- [x] Add regression coverage for the predicate and for both penalty outcomes.
- [x] Run focused unit tests, lint/format, type check, architecture check, diff check, and strict OpenSpec validation.
