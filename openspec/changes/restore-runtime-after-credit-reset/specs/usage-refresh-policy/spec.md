## ADDED Requirements
### Requirement: Confirmed reset restores existing router eligibility
A confirmed credit reset with fresh available quota SHALL restore eligibility in the existing router without pause/reactivate or restart. Recovery SHALL preserve manual pauses, authentication blocks, newer failures and non-quota backoff.
#### Scenario: Same router after reset
- **WHEN** a quota-limited account successfully redeems a reset and fresh usage confirms recovery
- **THEN** the same router SHALL select that account without retaining the recovered quota cooldown
#### Scenario: Unrelated block
- **WHEN** reset fails or the account has a newer or non-quota block
- **THEN** recovery SHALL NOT clear that block
#### Scenario: Delayed live sample
- **WHEN** a live sample queued before the reset refresh is persisted afterward
- **THEN** its observation timestamp SHALL retain its earlier ordering and SHALL NOT supersede newer reset usage
- **AND** genuinely newer live usage SHALL remain authoritative
#### Scenario: Ambiguous relative deadline
- **WHEN** only a relative reset duration is supplied for the blocked window
- **THEN** processing delay SHALL NOT be accepted as evidence that its absolute deadline moved
#### Scenario: Optional usage metadata
- **WHEN** a refreshed primary window omits its duration or an older fetch supplied a secondary row
- **THEN** valid primary evidence SHALL remain usable and only applicable sibling windows SHALL constrain recovery
