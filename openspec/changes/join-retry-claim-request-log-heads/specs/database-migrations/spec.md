## ADDED Requirements

### Requirement: Retry receipt and request-log migrations have one head

The migration graph MUST join the retry-claim receipt and request-log missing-cost index branches without rewriting either history.

#### Scenario: Upgrade from either branch

- **GIVEN** a database on either parent revision
- **WHEN** it upgrades to head
- **THEN** both branches are applied and a single merge revision is current
