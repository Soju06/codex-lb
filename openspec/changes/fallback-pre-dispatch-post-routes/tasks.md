## 1. Contract

- [x] 1.1 Define the pre-dispatch-only fallback rule for routed HTTP requests.
- [x] 1.2 Preserve the existing no-replay rule for dispatched and TLS-verification failures.

## 2. Regression Coverage

- [x] 2.1 Add a Codex client test proving a pre-dispatch connector failure on POST uses the next pool endpoint.
- [x] 2.2 Keep coverage proving a non-pre-dispatch POST failure does not fall back.

## 3. Implementation And Verification

- [x] 3.1 Implement the minimal Codex client fallback decision change.
- [x] 3.2 Run focused tests, related tests, lint/type checks, and OpenSpec validation.
- [x] 3.3 Verify the supported 8800, 7897, and 10020 routes plus pre-dispatch fallback and cancellation recovery on isolated port 2456; keep 9674 excluded.
- [x] 3.4 Restart production through the canonical launcher with routing disabled and stable 8800 egress, then verify production health.
