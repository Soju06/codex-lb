## ADDED Requirements

### Requirement: Endpointless ring members MUST remain relay-addressable

When a live bridge ring member has no explicit advertised endpoint metadata, the system MUST resolve that member to an internal HTTP endpoint derived from its bridge instance id if the instance id is a safe hostname token. Explicit `endpoint_base_url` metadata MUST remain authoritative when present. Malformed or unsafe instance ids MUST NOT produce derived endpoints.

#### Scenario: Blue-green retained owner has no endpoint metadata

- **GIVEN** two active bridge ring members
- **AND** the retiring owner has no endpoint metadata
- **AND** its bridge instance id is a safe hostname token
- **WHEN** another replica resolves that owner's endpoint
- **THEN** resolution returns `http://<instance-id>:2455`

#### Scenario: Unsafe instance id has no endpoint metadata

- **GIVEN** an active bridge ring member with no endpoint metadata
- **AND** its bridge instance id contains URL separators
- **WHEN** another replica resolves that owner's endpoint
- **THEN** no endpoint is derived
