## ADDED Requirements
### Requirement: Transport drops preserve account availability
The proxy MUST NOT penalize an account for `stream_incomplete` without an HTTP status, including upstream WebSocket receive failures, unless structured adapter provenance identifies an account-attributable protocol error or upstream-authored close. Known HTTP rejections and malformed non-streaming HTTP response objects MUST retain existing health handling, including invalid output items and malformed background acknowledgements.

#### Scenario: Mid-stream transport failure
- **GIVEN** an upstream stream terminates with `stream_incomplete` and no HTTP status
- **WHEN** the stream error handler records the outcome
- **THEN** it SHALL preserve the incomplete request outcome without account error, cooldown, quota, or permanent-failure writes

#### Scenario: Malformed HTTP JSON response retains its penalty
- **GIVEN** a non-streaming upstream HTTP response contains invalid output or a malformed background acknowledgement
- **WHEN** request settlement reports an incomplete response
- **THEN** account health SHALL retain the error penalty and the client SHALL receive the existing contract error

### Requirement: Transient backoff retains a recovery candidate
When backoff fallback is enabled and no normally selectable account remains, selection MUST keep the transient-backoff candidate nearest to recovery eligible, including in a single-account pool. It MUST NOT bypass rate-limit cooldown, quota exhaustion, paused, deactivated, or expired authentication states.

#### Scenario: Last healthy account is transiently backed off
- **GIVEN** one account without hard state remains in transient error backoff
- **WHEN** account selection runs with backoff fallback enabled
- **THEN** that account SHALL remain selectable without clearing its penalty history

#### Scenario: Hard states remain excluded
- **GIVEN** every account has a hard blocking state
- **WHEN** account selection runs
- **THEN** no account SHALL become eligible through transient backoff fallback
