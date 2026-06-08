## ADDED Requirements

### Requirement: Free-account quota surfaces are monthly-only

When an account's normalized quota model is monthly-only, account-facing quota surfaces SHALL present only the monthly window and MUST NOT render synthetic 5h or 7d bars for that account.

#### Scenario: Account surfaces show only monthly quota
- **WHEN** an account summary carries a normalized monthly quota window with no normalized 5h or 7d windows
- **THEN** the account card, account list row, and account detail usage panel show a single `Monthly` quota bar
- **AND** those surfaces do not render `5h` or `Weekly` bars for that account

### Requirement: Free-account overview quota hides 5h and 7d semantics

Overview and aggregate quota surfaces SHALL treat normalized monthly-only free-account quota as a 30d window and MUST NOT present that account as a weekly-only or dual-window account.

#### Scenario: Overview uses monthly semantics for free accounts
- **WHEN** overview data includes a free account with only a normalized monthly quota window
- **THEN** the overview account quota display shows only the 30d window for that account
- **AND** the account and API navigation progress logic uses monthly-only quota state for that account

### Requirement: Monthly quota remains visible in recent-trend displays

The account usage trend SHALL preserve the recent 7-day trend timeframe while identifying monthly-only quota lines as monthly quota.

#### Scenario: Monthly account trend labels the monthly line
- **WHEN** an account trend view renders a monthly-only free account
- **THEN** the trend legend identifies the quota line as `Monthly`
- **AND** the trend view still identifies itself as a 7-day trend

### Requirement: Zero-credit assigned accounts are omitted from 5h and weekly donut totals

Aggregate quota donuts SHALL omit assigned accounts whose visible assigned credits for the corresponding donut are zero.

#### Scenario: Zero-credit account does not contribute to donut totals
- **WHEN** an assigned account has zero visible credits for a 5h or weekly donut calculation
- **THEN** that account is excluded from the corresponding donut total and legend contributions
