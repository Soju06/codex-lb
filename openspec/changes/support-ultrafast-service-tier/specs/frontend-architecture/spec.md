## ADDED Requirements

### Requirement: API key service-tier controls include ultrafast
The API key create and edit controls MUST include `ultrafast` as an enforced service-tier option alongside `auto`, `default`, `priority`, and `flex`.

#### Scenario: Admin selects ultrafast enforced tier
- **WHEN** an admin opens the API key create or edit dialog
- **THEN** the enforced service-tier selector includes an `Ultrafast` option with value `ultrafast`
