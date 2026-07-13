## ADDED Requirements

### Requirement: Upstream rate-limit cooldown honors the Retry-After hint duration

The account cooldown SHALL last for the full duration expressed by a "try
again in" hint on an upstream rate-limit error. The parser SHALL
recognize hour, minute, second, and millisecond units, including their word
forms, and SHALL sum compound hints such as `1h2m3s` into a single duration.
A unit token SHALL be recognized only when it is not immediately followed by
another letter, so an unsupported longer word whose prefix matches a unit (for
example `month`, where `m` prefixes the word) is not mis-read as that shorter
unit. When the hint contains no recognizable unit token, the system SHALL fall
back to the error-count backoff schedule. A rate-limited account SHALL NOT be
re-selected before its cooldown elapses.

#### Scenario: Compound minute-and-second hint sets the full cooldown

- **GIVEN** an upstream 429 whose message says "try again in 6m0s"
- **WHEN** the balancer records the rate limit for the account
- **THEN** the account cooldown lasts 360 seconds
- **AND** the account is not re-selected until that cooldown elapses

#### Scenario: Minutes-only hint is honored

- **GIVEN** an upstream 429 whose message says "try again in 20m"
- **WHEN** the balancer records the rate limit for the account
- **THEN** the account cooldown lasts 1200 seconds

#### Scenario: Unparseable hint falls back to backoff

- **GIVEN** an upstream 429 whose message has no recognizable "try again in" duration
- **WHEN** the balancer records the rate limit for the account
- **THEN** the cooldown uses the error-count backoff schedule instead

#### Scenario: Unsupported longer word is not mis-read as a shorter unit

- **GIVEN** an upstream 429 whose message says "try again in 1 month"
- **WHEN** the balancer records the rate limit for the account
- **THEN** the `month` token is not read as a 1-minute hint
- **AND** the cooldown uses the error-count backoff schedule instead
