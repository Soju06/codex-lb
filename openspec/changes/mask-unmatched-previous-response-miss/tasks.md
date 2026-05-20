## 1. Implementation

- [x] Record fail-closed diagnostics when direct websocket handling masks an
      unmatched upstream previous_response_not_found event.
- [x] Record fail-closed diagnostics when HTTP bridge handling encounters an
      unmatched upstream previous_response_not_found event.
- [x] Preserve retryable stream_incomplete downstream behavior for any safely
      identified pending request.
- [x] Include the upstream error code in owner-unavailable continuity diagnostics.

## 2. Tests

- [x] Cover direct websocket unmatched previous-response masking diagnostics.
- [x] Cover HTTP bridge unmatched previous-response masking diagnostics.
- [x] Run focused proxy tests and OpenSpec validation.
