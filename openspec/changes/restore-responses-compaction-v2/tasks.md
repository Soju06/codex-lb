## 1. Specification

- [x] 1.1 Record the stream-first and legacy-fallback contract.

## 2. Implementation

- [x] 2.1 Add Responses-stream compaction collection to the upstream compact client.
- [x] 2.2 Keep legacy JSON compaction as unsupported-route fallback.
- [x] 2.3 Preserve route, timeout, archive, and error handling behavior.

## 3. Verification

- [x] 3.1 Add focused stream-success and fallback regression tests.
- [x] 3.2 Run focused unit tests, lint/type checks, and OpenSpec validation.
- [x] 3.3 Back up and synchronize the installed local runtime.
- [x] 3.4 Restart `codex-lb` and verify a real compact request path.
