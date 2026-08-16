# deployment-installation Delta

## ADDED Requirements

### Requirement: External secret references support provider-native layouts

When `externalSecrets.enabled=true`, the Helm chart MUST render an
`external-secrets.io/v1` ExternalSecret. The database URL and encryption key
MUST each accept an independent remote key and an optional JSON property. An
empty remote key MUST default to the release fullname, and the default
properties MUST preserve the existing `database-url` and `encryption-key` JSON
layout. Explicitly nulled remote reference overrides MUST render the default
layout instead of failing the template.

#### Scenario: Existing JSON secret layout remains the default

- **WHEN** external secrets mode is enabled without remote reference overrides
- **THEN** both target keys read from the remote secret named after the release
- **AND** they extract the `database-url` and `encryption-key` JSON properties
- **AND** the rendered ExternalSecret uses `external-secrets.io/v1`

#### Scenario: Individual remote secrets need no JSON property

- **WHEN** an operator configures separate absolute remote keys for the database URL and encryption key
- **AND** leaves both property values empty
- **THEN** each target key reads the complete value of its configured remote secret
- **AND** the rendered remote references omit `property`

#### Scenario: Nulled overrides fall back to the default layout

- **WHEN** an operator explicitly nulls `externalSecrets.remoteRefs` or one of its subtrees
- **THEN** rendering succeeds
- **AND** the affected target keys use the release fullname and their default JSON properties
