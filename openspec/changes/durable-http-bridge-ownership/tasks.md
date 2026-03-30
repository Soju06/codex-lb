## 1. Spec

- [x] 1.1 Add Responses HTTP bridge requirements for signed turn-state tokens and durable live-owner tracking
- [x] 1.2 Update bridge context and ops notes for stale recovery and new error codes
- [x] 1.3 Validate OpenSpec changes

## 2. Tests

- [x] 2.1 Update HTTP bridge tests that currently expect missing local aliases to fail with `bridge_instance_mismatch`
- [x] 2.2 Add regression coverage for `bridge_wrong_instance`, `bridge_session_expired`, and `bridge_token_invalid`
- [x] 2.3 Add coverage for stale-session recovery without `previous_response_id`

## 3. Implementation

- [x] 3.1 Add durable HTTP bridge lease storage and repository wiring
- [x] 3.2 Issue signed HTTP bridge turn-state tokens and validate replayed tokens
- [x] 3.3 Recover stale bridge sessions when continuity is not required
- [x] 3.4 Preserve fail-closed behavior only for true live owner mismatches
