## 1. Streaming latency budget and timeout defaults

- [x] 1.1 Add a configurable total request budget for streaming Responses requests
- [x] 1.2 Lower default upstream connect, stream idle, and token refresh timeouts to fail fast under instability
- [x] 1.3 Clamp effective upstream connect / idle / refresh timeouts to the remaining request budget on the request path

## 2. Retry and account-state policy

- [x] 2.1 Restrict automatic stream retries to account-recoverable failure classes
- [x] 2.2 Stop retrying generic upstream failures such as stalled streams or transport failures
- [x] 2.3 Avoid incrementing account transient error backoff for generic upstream instability that is not account-specific

## 3. Verification

- [x] 3.1 Add regression coverage for stalled stream timeout handling and timeout override wiring
- [x] 3.2 Add regression coverage proving non-retryable first-event failures are not retried
- [x] 3.3 Add regression coverage proving exhausted request budget returns a stable timeout failure event
- [x] 3.4 Validate specs with `openspec validate --specs`
