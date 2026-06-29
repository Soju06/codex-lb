## ADDED Requirements

### Requirement: Locally-generated account-selection retry hints fail fast in capacity wait
When the streaming Responses capacity-wait recovery path inspects an account-selection error message, the proxy MUST treat error messages produced by the local `_format_retry_hint` helper (the `Rate limit exceeded. Try again in <N>s` shape) as non-recoverable for the current request budget. The capacity-wait loop MUST NOT sleep on, and MUST NOT retry selection after, a locally-generated hint, regardless of the hint's `<N>` value. Upstream-derived recoverable messages (workspace spend cap, external retry-after hints not produced by `select_account`) MUST continue to use the existing recovery sleep behavior.

#### Scenario: Single-account pool with the only account rate-limited fails fast
- **GIVEN** a single-account proxy deployment whose only account is `RATE_LIMITED`
- **WHEN** a streaming Responses request enters the capacity-wait loop and `select_account` returns its locally-generated `Rate limit exceeded. Try again in <N>s` message
- **THEN** the capacity-wait loop does not sleep on that message
- **AND** the request fails immediately through the normal no-account or rate-limit error path instead of waiting up to the capped recovery interval

#### Scenario: Multi-account pool with every eligible account rate-limited fails fast
- **GIVEN** a multi-account proxy deployment where every account that would otherwise be eligible is simultaneously `RATE_LIMITED`
- **WHEN** a streaming Responses request enters the capacity-wait loop and `select_account` returns its locally-generated retry hint
- **THEN** the capacity-wait loop does not sleep on that message
- **AND** the request fails immediately instead of looping over the same locally-generated hint each iteration

#### Scenario: Upstream-derived recoverable message still uses the recovery sleep path
- **GIVEN** account selection surfaces an upstream-derived recoverable error message that is not formatted by `_format_retry_hint`
- **AND** the streaming request still has remaining request budget
- **WHEN** the capacity-wait loop inspects that message
- **THEN** the loop uses the existing bounded recovery sleep path
- **AND** retries selection after the bounded wait
