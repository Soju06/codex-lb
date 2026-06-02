## MODIFIED Requirements

### Requirement: Usage refresh is account-slot scoped

Usage refresh MUST write usage and change account status only for the credential slot being refreshed. It MUST NOT apply a payload that proves a different workspace identity to the target account.

#### Scenario: Mismatched workspace payload is ignored

- **GIVEN** an account has stored workspace identity
- **WHEN** usage refresh receives a payload for a different workspace
- **THEN** no usage rows are written for the account
- **AND** the account status, plan type, workspace metadata, and seat type are not changed

#### Scenario: Unknown workspace plan mismatch is non-destructive

- **GIVEN** an account has no stored workspace identity
- **WHEN** usage refresh receives a payload whose plan type conflicts with the stored non-unknown plan
- **THEN** no usage rows are written for the account
- **AND** the account status and plan type are not changed
