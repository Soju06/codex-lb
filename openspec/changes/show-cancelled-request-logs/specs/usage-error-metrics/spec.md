## ADDED Requirements

### Requirement: Cancelled request logs remain visible and distinct

The Request Logs operator surface MUST include persisted
`status='cancelled'` rows in its unfiltered listing and total. Such rows MUST
be exposed with public status `cancelled`, MUST be available through a
`cancelled` status option and filter, and MUST NOT be returned by the `error`
status filter. The dashboard MUST render the status with localized cancelled
copy and a visual treatment distinct from error.

#### Scenario: Unfiltered Request Logs include cancellations

- **GIVEN** one persisted cancelled request and one persisted genuine error
- **WHEN** an operator requests the unfiltered Request Logs list
- **THEN** both requests are returned and included in the total
- **AND** the cancelled request exposes public status `cancelled`

#### Scenario: Cancelled and error filters remain separate

- **GIVEN** one persisted cancelled request and one persisted genuine error
- **WHEN** an operator filters Request Logs by `cancelled`
- **THEN** only the cancelled request is returned
- **AND WHEN** the operator filters Request Logs by `error`
- **THEN** only the genuine error is returned

#### Scenario: Dashboard presents a cancelled status

- **GIVEN** Request Logs contain a persisted cancelled request
- **WHEN** the dashboard loads status options and renders the request
- **THEN** the status filter includes a localized Cancelled option
- **AND** the row and request details use the localized cancelled label
- **AND** the cancelled badge is visually distinct from the error badge
