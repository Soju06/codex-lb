## 1. Contract

- [x] 1.1 Define the cardinality-independent warmup failure contract and implementation boundaries.
- [x] 1.2 Sync the clarified requirement to the main `proxy-warmup` specification.

## 2. Regression and implementation

- [x] 2.1 Add production FastAPI integration coverage for one-account auth and rate-limit failures and capture the failing baseline.
- [x] 2.2 Remove only the single-account conditional re-raise for `ProxyAuthError` and `ProxyRateLimitError`.

## 3. Verification

- [x] 3.1 Capture focused GREEN and adjacent warmup integration results.
- [x] 3.2 Run strict OpenSpec validation, affected lint/type checks, and production FastAPI surface proof.
- [x] 3.3 Review the committed diff independently and address in-scope findings.
