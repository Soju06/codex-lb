## ADDED Requirements

### Requirement: Accounts list supports explicit sort modes

The Accounts page account list SHALL expose sort modes for reset time
soonest-first, reset time latest-first, account name ascending, and account name
descending. The default sort mode SHALL remain reset time soonest-first. The
same selected sort mode SHALL apply to both the rendered account list and the
page-level selected-account fallback.

#### Scenario: Reset soonest remains the default

- **WHEN** the account list renders without an explicit sort mode
- **THEN** accounts with the earliest upcoming visible quota reset sort first

#### Scenario: Reset latest sorts finite resets descending

- **WHEN** a user selects reset time latest-first
- **THEN** accounts with later upcoming visible quota resets sort before
  accounts with earlier upcoming visible quota resets
- **AND** accounts without an upcoming visible reset timestamp sort after
  accounts with finite upcoming reset timestamps

#### Scenario: Name sort modes order by account label

- **WHEN** a user selects account name ascending or descending
- **THEN** the account list orders accounts by display name, email, or account
  identifier in the selected direction
