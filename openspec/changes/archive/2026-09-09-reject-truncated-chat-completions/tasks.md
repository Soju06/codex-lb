## 1. Specification

- [x] 1.1 Define streaming and non-streaming EOF truncation requirements.
- [x] 1.2 Validate the scoped OpenSpec change.

## 2. Regression Coverage

- [x] 2.1 Add a streaming adapter regression for error chunk plus `[DONE]`.
- [x] 2.2 Add a collected adapter regression for the canonical error envelope.
- [x] 2.3 Add a non-streaming route regression for HTTP 502 and
  `upstream_stream_truncated`.

## 3. Implementation

- [x] 3.1 Track terminal event observation in the Chat adapter.
- [x] 3.2 Synthesize canonical truncation errors without changing explicit
  terminal behavior.

## 4. Verification

- [x] 4.1 Run focused Chat adapter and route tests.
- [x] 4.2 Manually verify streaming and non-streaming HTTP surfaces.
- [x] 4.3 Run lint, type, diagnostic, and strict OpenSpec gates.
