## 1. Implementation

- [x] 1.1 Add a shared helper that decides when dynamic bridge-ring DB membership is required.
- [x] 1.2 Skip startup register/heartbeat tasks when dynamic ring membership is disabled.
- [x] 1.3 Skip request-path ring membership DB lookups when dynamic ring membership is disabled.

## 2. Verification

- [x] 2.1 Add unit tests for the helper and startup behavior.
- [x] 2.2 Run targeted backend tests for ring membership and startup behavior.
