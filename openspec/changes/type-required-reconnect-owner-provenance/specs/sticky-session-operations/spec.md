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

### Requirement: File-pin continuity provenance bypasses only single-account routing

Account selection MUST skip single-account pool narrowing when a required
preferred account is a continuity owner and file-pin ownership requests that
override. API-key assignment scope, security authorization, and typed
continuity miss or policy-conflict handling MUST remain unchanged. A
previous-response or account-neutral continuity owner without that override
MUST still intersect single-account policy.

#### Scenario: File-pin continuity owner is not narrowed to the dashboard account

- **GIVEN** a required preferred account is a continuity owner
- **AND** file-pin ownership requests a single-account override
- **AND** dashboard single-account routing selects a different account
- **WHEN** account selection runs
- **THEN** selection MUST return the file-pin owner when that owner is otherwise eligible
- **AND** it MUST NOT narrow `account_ids` to the dashboard single-account id

#### Scenario: Previous-response continuity owner still intersects single-account policy

- **GIVEN** a required preferred account is a continuity owner without a file-pin override
- **AND** dashboard single-account routing selects a different account
- **WHEN** account selection runs
- **THEN** selection MUST keep single-account narrowing
- **AND** a miss MUST remain typed `continuity_owner_policy_conflict` when the owner is outside that policy

#### Scenario: File-pin single-account override does not drop API-key assignment scope

- **GIVEN** a required file-pin continuity owner requests a single-account override
- **AND** the API key assignment scope excludes that owner
- **WHEN** account selection runs
- **THEN** selection MUST still pass the API-key assignment scope into the owner lookup
