## 1. Spec

- [x] 1.1 Add a runtime observability capability spec for timestamped console logs and upstream request tracing

## 2. Runtime logging

- [x] 2.1 Add canonical timestamped console log formatting to the server entrypoint
- [x] 2.2 Add outbound upstream request start/completion tracing with optional payload logging
- [x] 2.3 Log proxy 4xx/5xx responses with error code and message

## 3. Verification

- [x] 3.1 Add unit coverage for CLI log config wiring
- [x] 3.2 Add unit coverage for upstream request tracing and proxy error logging
- [x] 3.3 Run targeted pytest slices for the changed logging paths
