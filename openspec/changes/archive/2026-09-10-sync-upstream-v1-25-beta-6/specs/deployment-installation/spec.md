## ADDED Requirements

### Requirement: Beta.6 upgrades preserve deployed fork key groups
An upgrade to the integrated beta.6 release MUST converge to a single migration head from either the fork's deployed API-key usage-group schema, the upstream report-rollup schema, or an empty database. It MUST preserve existing API-key identities, usage groups, limits, and group lookup indexes. Previously deployed migration identities and parent relationships MUST remain unchanged.

#### Scenario: Upgrade an existing grouped-key database
- **GIVEN** a database at the deployed fork usage-group revision with a key in group `team-a`
- **WHEN** it is upgraded to the integrated head
- **THEN** the key retains its identity, group, and limits
- **AND** the report-rollup schema and group lookup index are present
- **AND** there is exactly one current migration head

#### Scenario: Upgrade an upstream or fresh database
- **WHEN** a database at the upstream beta.6 head or an empty database is upgraded
- **THEN** both report-rollup and usage-group schemas exist at the same single head
