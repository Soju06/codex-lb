## ADDED Requirements
### Requirement: Confirmed reset restores existing router eligibility
A confirmed credit reset with fresh available quota SHALL restore eligibility in the existing router without pause/reactivate or restart. Recovery SHALL preserve manual pauses, authentication blocks, newer failures and non-quota backoff.
#### Scenario: Same router after reset
- **WHEN** a quota-limited account successfully redeems a reset and fresh usage confirms recovery
- **THEN** the same router SHALL select that account without retaining the recovered quota cooldown
#### Scenario: Unrelated block
- **WHEN** reset fails or the account has a newer or non-quota block
- **THEN** recovery SHALL NOT clear that block
