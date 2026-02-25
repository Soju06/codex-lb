## ADDED Requirements

### Requirement: Credentials Batch Import API

The dashboard SHALL provide `POST /api/accounts/import-credentials` to import multiple accounts from a multi-line credential text payload. Each non-empty line MUST use the format `email:password:2fa_secret`.

#### Scenario: Successful multi-account import

- **WHEN** the request body contains valid credential lines
- **THEN** the API performs automated authorization for each line
- **AND** returns `200` with `total`, `imported`, `failed`, and per-line `results`

#### Scenario: Strict format validation

- **WHEN** any line is malformed (missing delimiters, missing email/password/2fa secret)
- **THEN** the API returns `400`
- **AND** `error.code` is `invalid_credentials_format`
- **AND** no authorization attempt is executed

### Requirement: Per-account execution isolation

Automated authorization errors for one credential line SHALL NOT abort the entire batch execution.

#### Scenario: Partial failure

- **WHEN** one account authorization fails during execution
- **THEN** other accounts continue processing
- **AND** the failed entry is returned with `status=failed` and a non-empty `error`

### Requirement: Sensitive credential handling

Credential values (`password`, `2fa_secret`) MUST be treated as transient input and MUST NOT be persisted in database models.

#### Scenario: Persist only OAuth tokens and account metadata

- **WHEN** an account is authorized successfully
- **THEN** only account identity/plan/tokens and existing account fields are stored
- **AND** raw credential material is discarded after processing
