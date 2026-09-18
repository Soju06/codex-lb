## ADDED Requirements

### Requirement: API-key dialogs manage estimated usage-share caps

The API-key create and edit dialogs SHALL expose one optional integer field labelled `Estimated pool allocation (%)`. It MUST accept 1 through 100, permit clearing, and serialize as `usageSharePercent`. It SHALL be visually separate from fixed token and dollar limits and explain that it caps the key's estimated subscription-backed generation usage at that share of its account pool, follows quota resets and pool membership, and excludes model-source, file, control, thread-goal, and realtime traffic.

API-key table and detail surfaces SHALL present the configured percentage as an estimated pool cap and MUST NOT label it exact billing or current exact consumption.

#### Scenario: Create an estimated-share key

- **WHEN** an administrator enters 20 and creates the key
- **THEN** the request contains `usageSharePercent: 20`
- **AND** the returned key displays a 20 percent estimated pool cap

#### Scenario: Clear an existing estimated share

- **GIVEN** an existing key has a 20 percent policy
- **WHEN** the field is cleared and saved
- **THEN** the patch contains `usageSharePercent: null`

#### Scenario: Unrelated edits preserve the share

- **GIVEN** a key has a configured estimated usage share
- **WHEN** an administrator edits only an unrelated field
- **THEN** the patch omits `usageSharePercent`
- **AND** the existing share is preserved

#### Scenario: Fixed limits remain separate

- **WHEN** a key has both a usage-share cap and fixed limits
- **THEN** the dialog presents them separately
- **AND** editing one does not reset the other

#### Scenario: Allocation-only key is not described as unlimited

- **GIVEN** a key has an estimated pool allocation and no fixed token or dollar rules
- **WHEN** the table or detail view renders the key
- **THEN** it displays the estimated pool cap
- **AND** it MUST NOT label the key `No Limit` or say that no limits are configured
- **AND** any empty-state copy is explicitly scoped to fixed limits
