## ADDED Requirements

### Requirement: Account sorting by status and remaining quota

The Accounts sort selector SHALL offer ascending and descending ordering by status and remaining 5-hour, weekly, and monthly quota percentages. Status order SHALL be active, paused, rate-limited, quota-exceeded, reauthentication-required, then deactivated; descending reverses this order. Missing quota values SHALL sort after known values in both directions, and zero SHALL be treated as a known value. Ties SHALL use the existing deterministic reset-time, name, and account-ID order. The selected ordering SHALL apply before filtering and SHALL remain selected while the Accounts page is mounted. Existing sort modes and the default SHALL retain their behavior.

#### Scenario: Lowest remaining monthly quota
- **WHEN** the operator selects lowest remaining monthly quota
- **THEN** an account at zero percent appears before one at 80 percent
- **AND** an account with unknown monthly quota appears after both

#### Scenario: Highest remaining quota
- **WHEN** the operator selects highest remaining quota
- **THEN** known values appear in descending order and missing values remain last

#### Scenario: Status order and filtering
- **WHEN** the operator selects active-first status ordering and filters the account list
- **THEN** the selected mode remains active and active accounts precede paused accounts
