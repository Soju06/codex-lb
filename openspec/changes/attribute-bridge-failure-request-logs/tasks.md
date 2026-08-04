## 1. Bridge failure fan-out attribution

- [x] 1.1 Attribute each pending request's error log row to
      `request_state.api_key`, with the caller-provided key as fallback.

## 2. Validation

- [x] 2.1 Add a regression: bridge failure fan-out with `api_key=None` still
      attributes the pending request's own key.
- [x] 2.1b Add a route-level regression: an authenticated `/v1/responses`
      bridge request failing through the fan-out (upstream send failure)
      persists its `RequestLog.api_key_id`.
- [x] 2.2 Run focused tests, lint, type checking, and strict OpenSpec
      validation.
