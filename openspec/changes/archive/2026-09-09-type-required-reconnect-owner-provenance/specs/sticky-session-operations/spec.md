## ADDED Requirements

### Requirement: File-pin reconnect provenance preserves existing routing eligibility

HTTP-bridge reconnect MUST mark a live required file-pin owner as a continuity
owner. Existing account-neutral replay provenance MUST remain unchanged. A
non-file previous-response or other require-preferred owner MUST retain its
ordinary required-preferred provenance and its existing single-account and
API-key assignment-scope eligibility semantics.

#### Scenario: File-pin reconnect carries continuity provenance

- **GIVEN** a live file pin requires `account_a` during HTTP-bridge reconnect
- **WHEN** reconnect selects an account
- **THEN** it MUST pass `account_a` as a required preferred account
- **AND** it MUST mark `account_a` as a continuity owner
- **AND** it MUST disable fallback to another account

#### Scenario: Previous-response owner retains required-preferred semantics

- **GIVEN** a non-file previous-response reconnect requires `account_b`
- **WHEN** reconnect selects an account
- **THEN** it MUST pass `account_b` as a required preferred account
- **AND** it MUST NOT newly mark `account_b` as a continuity owner
- **AND** dashboard single-account routing MUST NOT narrow that required owner
- **AND** API-key assignment scope MUST still determine its eligibility

### Requirement: File-pin provenance preserves single-account behavior without weakening scope

A required file-pin continuity owner MUST bypass dashboard single-account
narrowing, matching the existing required-preferred behavior. It MUST NOT
become eligible outside API-key assignment scope, and security authorization
scope MUST remain unchanged.

#### Scenario: Dashboard account differs from in-scope file owner

- **GIVEN** dashboard single-account routing selects `account_x`
- **AND** an in-scope live file pin requires `account_a`
- **WHEN** reconnect selection runs
- **THEN** it MUST select only `account_a`
- **AND** it MUST NOT narrow the lookup to `account_x`

#### Scenario: File owner is outside API-key assignment scope

- **GIVEN** a live file pin requires `account_a`
- **AND** the API key assignment scope excludes `account_a`
- **WHEN** reconnect selection runs
- **THEN** `account_a` MUST remain ineligible
- **AND** selection MUST NOT serve the reconnect from an out-of-scope account
