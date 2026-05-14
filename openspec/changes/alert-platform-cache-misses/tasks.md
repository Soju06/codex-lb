## 1. Alert Core

- [x] 1.1 Add optional cache-alert runtime settings with safe defaults.
- [x] 1.2 Implement a redacted Platform cache-miss observer with 7-request rolling windows, 4-miss threshold, timeout, and cooldown.
- [x] 1.3 Add unit tests for thresholding, cache-hit suppression, suffix-only payloads, disabled mode, and cooldown.

## 2. Proxy Integration

- [x] 2.1 Derive the Platform API-key suffix from encrypted identities without exposing the raw key.
- [x] 2.2 Observe usage from non-streaming Platform Responses requests.
- [x] 2.3 Observe usage from streaming Platform Responses completion events.
- [x] 2.4 Observe usage from Platform compact Responses requests.
- [x] 2.5 Add integration coverage that a Platform cache-miss window sends only the key suffix.

## 3. CI Repair and Validation

- [x] 3.1 Fix the PostgreSQL-only sticky-session retry regression from PR #21 CI.
- [x] 3.2 Run targeted tests for cache-alert behavior and sticky-session retry behavior.
- [x] 3.3 Run lint/type/spec validation and PostgreSQL-relevant tests before pushing.
