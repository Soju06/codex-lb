# account-identity Specification

## Purpose
TBD - created by archiving change fix-shared-workspace-account-slots. Update Purpose after archive.
## Requirements
### Requirement: Shared upstream workspace identities preserve account slots

The account import and OAuth add-account flows MUST preserve separate local account slots for different real email addresses even when the upstream token reports the same ChatGPT account id, with or without a workspace id.

Dashboard account summaries MUST expose and render the upstream ChatGPT account id as the primary workspace/account-slot context before falling back to optional workspace metadata or a generic unknown-workspace label.

#### Scenario: Shared workspace account ids preserve separate emails
- **GIVEN** two account credentials have different real email addresses
- **AND** both credentials report the same upstream ChatGPT account id
- **WHEN** the operator imports or adds both accounts through OAuth
- **THEN** the system persists separate local account slots for each email
- **AND** the second account does not overwrite the first account's stored email or tokens

#### Scenario: Workspace context uses ChatGPT account id
- **GIVEN** an account has a ChatGPT account id
- **WHEN** the dashboard renders the account workspace context
- **THEN** it displays the ChatGPT account id
- **AND** it does not display the generic unknown-workspace label

### Requirement: Verified OAuth Live callers resolve to a stable principal

The system SHALL validate a supplied ChatGPT bearer and `chatgpt-account-id` against the upstream usage endpoint before trusting identity claims. It SHALL derive a stable principal from verified per-seat claims. A matching imported seat MAY retain its internal Account id for affinity compatibility; a verified caller without an imported Account SHALL remain authorized through its independent principal. A credential without a stable claim SHALL require one unambiguous imported seat.

#### Scenario: External OAuth caller is accepted

- **GIVEN** valid OAuth credentials carry a stable seat claim and no imported Account matches them
- **WHEN** the caller reaches a Live Voice route
- **THEN** upstream credential validation succeeds through the configured default route
- **AND** the caller receives an independent stable principal

#### Scenario: Imported caller uses its Account affinity

- **GIVEN** a verified caller matches one imported seat
- **WHEN** the resolver derives its principal
- **THEN** the existing internal Account id remains the affinity material
- **AND** repeated Live requests retain a stable caller scope

#### Scenario: Identity remains ambiguous

- **WHEN** credentials expose no stable seat claim and cannot resolve one eligible imported seat
- **THEN** identity resolution fails before policy lookup and upstream account selection
- **AND** the response reveals no identity or candidate details

### Requirement: OAuth identity validation is bounded and coalesced

Cache and singleflight keys MUST be a one-way digest over bearer and normalized `chatgpt-account-id`. Concurrent misses for the same pair MUST share one upstream validation. Positive entries MUST expire within 60 seconds and token expiry; credential-denial entries MUST expire within 5 seconds. Upstream availability and rate-limit failures MUST preserve typed failure semantics.

#### Scenario: Concurrent validation misses are coalesced

- **GIVEN** the same uncached bearer and normalized `chatgpt-account-id` reach OAuth identity validation concurrently
- **WHEN** upstream validation is still in flight
- **THEN** all callers await one shared validation
- **AND** cache keys and diagnostics expose no raw credential
