## 1. Error contract

- [x] Preserve `usage_limit_reached` from pool-wide account selection failures.
- [x] Map pool-wide usage exhaustion to HTTP 429 with OpenAI-style
      `error.code = "usage_limit_reached"` and
      `error.type = "usage_limit_reached"`.
- [x] Preserve `error.resets_at` when account selection provides a reset hint.

## 2. Proxy surfaces

- [x] Apply the same selection-failure response helper across HTTP, streaming,
      bridge, compact, file, transcription, WebSocket, and Codex-control paths.
- [x] Keep local capacity cap errors as 429 `rate_limit_error` responses rather
      than weakening their existing contract.

## 3. Regression coverage

- [x] Add unit coverage for pool usage exhaustion selection and response mapping.
- [x] Add externally routed HTTP/streaming regressions for the 429 envelope.

## 4. Validation

- [x] Run focused pytest for selection, load balancer, and Responses proxy
      regressions.
- [x] Run lint/type checks for touched Python files.
- [x] Validate the OpenSpec change strictly.
