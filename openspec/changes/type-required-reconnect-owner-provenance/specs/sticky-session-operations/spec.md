## ADDED Requirements

### Requirement: Required HTTP-bridge reconnect owners use continuity-owner selection provenance

HTTP-bridge reconnect MUST mark a preferred account as a continuity owner if
and only if that account is a required reconnect owner. A live file pin,
`require_preferred_account`, and account-neutral recovery populate that
required owner. Movable soft reconnect MUST NOT mark an ordinary preferred
account or a skippable closed account as a continuity owner.

#### Scenario: Required reconnect owner is typed as continuity provenance

- **GIVEN** HTTP-bridge reconnect resolved a required preferred account
- **WHEN** account selection runs for that reconnect
- **THEN** selection MUST receive `preferred_account_is_continuity_owner`
- **AND** a miss for that owner MUST be eligible to return typed `continuity_owner_unavailable`

#### Scenario: Movable soft reconnect is not typed as continuity provenance

- **GIVEN** a soft HTTP-bridge reconnect has no required preferred account
- **WHEN** account selection runs for that reconnect
- **THEN** selection MUST NOT receive continuity-owner provenance
- **AND** the closed account MAY still be skipped when the close code permits it
