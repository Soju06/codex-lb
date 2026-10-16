## ADDED Requirements

### Requirement: Explicit workspace deactivation is a scoped terminal refresh signal

Usage refresh MUST recognize the exact structured `deactivated_workspace` error code through the existing permanent-failure status policy and mark only the selected workspace account record `deactivated`. Bare HTTP 402 or 404 responses without a recognized permanent code or an explicit account-deactivation message MUST NOT change account status or routing availability.

#### Scenario: Workspace rejection during refresh excludes the selected record

- **GIVEN** two workspace account records for one identity
- **WHEN** usage refresh for one record returns `deactivated_workspace`
- **THEN** only that record is marked `deactivated`
- **AND** the other workspace record retains its status

#### Scenario: Ambiguous payment status preserves the account

- **WHEN** usage refresh receives HTTP 402 with no terminal code or explicit account-deactivation message
- **THEN** the account retains its status and routing availability
- **AND** the failure remains eligible for the existing later refresh retry policy
