# account-quota-presentation Specification

## Purpose
Governs how account quota windows are presented across the dashboard when an account does not fit the paid 5h/7d shape. Free accounts report a single monthly window, and rendering it as weekly produced wrong bars, trends, donut totals, and assigned-account badges. This capability keeps monthly-only accounts labelled as such, omits zero-credit accounts from window totals they cannot contribute to, and keeps quota refreshes visually stable.
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

### Requirement: Account quota refreshes preserve visual continuity

Account-facing quota surfaces MUST retain the last valid percentage when a
refresh briefly reports an unknown or non-finite value. They MUST keep the
corresponding quota row mounted for the same bounded hold and MUST NOT render
the unknown refresh state as zero. When a later valid percentage arrives, the
visible number and bar MUST ease from the displayed value to the new value at
0.1 percent display resolution. Reduced-motion preferences MUST update the
value without animation. Raw percentages used by sorting and routing MUST stay
unchanged.

#### Scenario: Temporary unknown value does not drain the bar

- **GIVEN** an account quota row displays a valid remaining percentage
- **WHEN** a refresh temporarily reports that percentage as unknown or non-finite
- **THEN** the account card, account list row, and account detail usage panel keep the last valid percentage visible
- **AND** the quota row remains mounted
- **AND** the bar does not drain to zero

#### Scenario: Fresh percentage replaces the held value smoothly

- **GIVEN** an account quota row is displaying a valid or held percentage
- **WHEN** a later refresh reports a different valid percentage
- **THEN** the displayed number and bar ease to the fresh percentage
- **AND** the visible number can change in 0.1 percent increments
- **AND** the raw percentage used by sorting and routing is unchanged

#### Scenario: Reduced motion skips the transition

- **GIVEN** the user prefers reduced motion
- **WHEN** a fresh valid percentage replaces the displayed percentage
- **THEN** the quota surface displays the fresh percentage without animation

