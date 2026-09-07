# account-quota-presentation Specification

## Purpose
Define how account quota windows, remaining usage, and configured caps are presented consistently across dashboard surfaces.
## Requirements
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

### Requirement: Account usage-cap controls and markers

The Accounts page SHALL offer independent optional 5h and weekly consumed-percentage cap controls for applicable windows, with existing configured values removable when a window disappears. Enabled caps SHALL expose a visually distinct disable button. Read-only users SHALL NOT be able to save changes. Individual account remaining-quota bars on the Accounts and Dashboard pages SHALL render quota below `100 - cap` percent remaining as a solid gray unavailable segment separated from usable quota by a narrow gap and SHALL label capped values with both total remaining and usable remaining percentages plus accessible descriptive text. Provider remaining percentages SHALL remain unchanged; monthly and additional-quota bars SHALL NOT display these cap segments.

#### Scenario: Reserved quota is visible
- **WHEN** a displayed 5h account bar has a configured cap of 80 percent used
- **THEN** the first 20 percent of its remaining-quota track is a solid gray unavailable segment
- **AND** a narrow gap separates it from usable remaining quota
- **AND** its value shows total remaining and usable remaining percentages
- **AND** its accessible description identifies the cap and remaining threshold

#### Scenario: Independent controls
- **WHEN** an operator enables only the weekly cap
- **THEN** the weekly threshold is editable and saved independently
- **AND** the 5h bar has no cap marker
