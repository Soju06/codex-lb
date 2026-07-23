## ADDED Requirements

### Requirement: Persisted security-work lineage is enforced across Responses transports

When a Responses lineage is marked as requiring a security-work-authorized
account, compact, streamed Responses, and HTTP bridge account selection MUST
enforce that requirement for every durable alias, including
`previous_response_id`. The requirement MUST remain monotonic when the original
account or durable bridge owner is no longer available.

A request newly classified by upstream as security work MAY use the existing
safe ordinary-account fallback when no authorized account is available. That
bounded compatibility fallback MUST NOT weaken a requirement that existed
before the request, and it MUST NOT repeatedly re-read the marker and re-enter
authorized-account selection.

#### Scenario: Previous-response continuation crosses transport

- **GIVEN** a bridge, compact, or streamed request persists a security-work
  requirement for a `previous_response_id`
- **WHEN** a later request presents that alias through another Responses
  transport
- **THEN** account selection requires a security-work-authorized account
- **AND** owner deletion or missing durable bridge state does not downgrade the
  requirement

#### Scenario: Persisted requirement has no authorized account

- **GIVEN** the request lineage was marked before the current request
- **AND** no eligible security-work-authorized account exists
- **WHEN** compact or streamed Responses account selection runs
- **THEN** the request fails with the no-authorized-account error or the
  original security-work authorization failure
- **AND** the request is not sent to an ordinary fallback account

#### Scenario: Newly classified request uses bounded compatibility fallback

- **GIVEN** the request lineage was not marked before the current request
- **AND** upstream newly classifies the request as requiring security-work
  authorization
- **AND** no eligible security-work-authorized account exists
- **WHEN** the transport applies its documented safe ordinary-account fallback
- **THEN** marker enforcement is bypassed only for that fallback selection
- **AND** selection does not loop back into the authorized-account requirement
