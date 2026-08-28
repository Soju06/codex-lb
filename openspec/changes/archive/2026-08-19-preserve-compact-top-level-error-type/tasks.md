## 1. Regression Coverage

- [x] 1.1 Add a routed compact top-level terminal SSE regression that preserves `invalid_request_error`
- [x] 1.2 Add missing and blank `error_type` fallback controls and retain the nested-envelope control

## 2. Compact Error Conversion

- [x] 2.1 Preserve a supplied non-blank top-level `error_type` in the OpenAI error detail
- [x] 2.2 Keep status, code, message, parameter, nested-envelope, and `server_error` fallback behavior unchanged

## 3. Verification

- [x] 3.1 Run focused compact tests, Ruff, type checking, proxy architecture checks, and strict affected OpenSpec validation
- [x] 3.2 Exercise the live compact HTTP route with top-level invalid-request and missing-type upstream terminal frames
- [x] 3.3 Verify implementation against this change, synchronize the delta, and archive the verified OpenSpec change
