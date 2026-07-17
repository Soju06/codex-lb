## MODIFIED Requirements

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

Explicit upstream reset metadata SHALL be accepted only when it resolves to a
finite deadline strictly later than the current time and no more than
`RATE_LIMIT_RESET_MAX_HORIZON_SECONDS` (366 days) in the future. `resets_at`
SHALL be interpreted as an absolute Unix timestamp and `resets_in_seconds`
SHALL be interpreted as a relative duration. When `resets_at` is invalid but
`resets_in_seconds` is valid, the relative duration SHALL be used. An accepted
fractional deadline SHALL be rounded up to the next whole second before
persistence. A persisted integer deadline produced by that rounding MAY be
less than one second beyond the raw 366-day horizon and MUST remain valid when
selection reconstructs it. When neither field is valid, the error SHALL be
treated as carrying no explicit reset metadata.

When the upstream rate-limit error carries no valid explicit reset metadata,
the resolved cooldown deadline SHALL be persisted on the account row
(`reset_at`) so the cooldown survives process restarts and is visible to all
replicas sharing the database: a parsed Retry-After hint deadline SHALL be
persisted rounded up to the next whole second (persistence stores `reset_at`
as an integer, so a short or fractional hint MUST NOT truncate down to an
already-elapsed deadline), and when the cooldown comes from the error-count
backoff fallback the persisted deadline SHALL be at least
`RATE_LIMITED_MIN_COOLDOWN_SECONDS` (30 seconds) in the future. The marking
replica's in-process cooldown MAY remain shorter than the persisted deadline
so its existing fresh-usage recovery gate is unchanged.

An already-persisted `rate_limited` reset deadline beyond the same plausibility
horizon SHALL be treated as missing metadata rather than as an unexpired
cooldown. A row carrying `blocked_at` SHALL still honor the existing 30-second
minimum floor and SHALL require recent usage evidence recorded after that block
before selection-time recovery may clear it. A row without `blocked_at` SHALL
require recent available usage evidence. In both cases, every applicable
derived quota window MUST report below `100%` usage before recovery.

#### Scenario: Compound minute-and-second hint sets the full cooldown

- **GIVEN** an upstream 429 whose message says "try again in 6m0s"
- **WHEN** the balancer records the rate limit for the account
- **THEN** the account cooldown lasts 360 seconds
- **AND** the account is not re-selected until its cooldown elapses

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

#### Scenario: Cooldown without upstream reset metadata is persisted

- **GIVEN** an upstream 429 that carries no `resets_at`/`resets_in_seconds` metadata and no parseable Retry-After hint
- **WHEN** the balancer records the rate limit for the account
- **THEN** the persisted account row holds status `RATE_LIMITED` with `blocked_at` set
- **AND** the persisted `reset_at` is at least 30 seconds in the future

#### Scenario: Retry-After hint deadline is persisted

- **GIVEN** an upstream 429 whose message says "try again in 20m" and that carries no reset metadata
- **WHEN** the balancer records the rate limit for the account
- **THEN** the persisted `reset_at` is approximately 1200 seconds in the future

#### Scenario: Short fractional Retry-After hint is not truncated away

- **GIVEN** an upstream 429 whose message says "try again in 500ms" and that carries no reset metadata
- **WHEN** the balancer records the rate limit for the account
- **THEN** the persisted integer `reset_at` deadline is strictly in the future
- **AND** peer replicas honor the hinted cooldown instead of reselecting the account immediately

#### Scenario: Plausible explicit reset metadata remains authoritative

- **GIVEN** an OpenAI service 429 carrying a finite `resets_at` deadline 30 days in the future
- **WHEN** the balancer records the rate limit for the account
- **THEN** the accepted explicit deadline is persisted
- **AND** the Retry-After/backoff fallback does not replace it

#### Scenario: Implausible explicit reset metadata uses the bounded fallback

- **GIVEN** an OpenAI service 429 carrying `resets_at=15023672358` while the current Unix time is approximately `1784146959`
- **AND** the error carries no valid `resets_in_seconds` or parseable duration
- **WHEN** the balancer records the rate limit for the account
- **THEN** the implausible absolute deadline is rejected
- **AND** the persisted deadline uses the minimum bounded backoff instead

#### Scenario: Valid relative metadata survives an invalid absolute value

- **GIVEN** an OpenAI service 429 whose `resets_at` is implausibly far in the future
- **AND** whose `resets_in_seconds` is a finite positive duration within 366 days
- **WHEN** the balancer records the rate limit for the account
- **THEN** the relative duration determines the persisted deadline

#### Scenario: Horizon-edge rounding remains stable

- **GIVEN** valid absolute or relative reset metadata resolves exactly 366 days after a fractional current timestamp
- **WHEN** the balancer rounds and persists the deadline to a whole second
- **THEN** persisted-state reconstruction continues to accept that deadline
- **AND** does not clear the cooldown solely because rounding crossed the raw horizon by less than one second

#### Scenario: Existing implausible deadline does not pin selection indefinitely

- **GIVEN** a persisted `rate_limited` account whose `reset_at` is more than 366 days in the future
- **AND** whose `blocked_at` minimum floor has elapsed
- **WHEN** selection reconstructs the account from fresh available usage evidence
- **THEN** the implausible deadline is treated as missing metadata
- **AND** normal compare-and-set recovery may restore the account to `active`

#### Scenario: Exhausted long-window quota prevents poisoned-row recovery

- **GIVEN** a persisted `rate_limited` account whose reset deadline is implausible
- **AND** a fresh primary window reports available quota
- **AND** an applicable weekly or monthly window reports `100%` usage
- **WHEN** selection reconstructs the account
- **THEN** the account remains `rate_limited`

#### Scenario: Implausible legacy deadline without a block marker recovers

- **GIVEN** a persisted `rate_limited` account whose reset deadline is implausible
- **AND** the row has no `blocked_at` marker
- **WHEN** selection reconstructs the account from recent available usage in every applicable window
- **THEN** normal compare-and-set recovery may restore the account to `active`
