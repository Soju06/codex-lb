## MODIFIED Requirements

### Requirement: Dashboard usage donuts present credits as stacked remaining and capacity

The dashboard's primary and secondary usage donuts MUST present remaining credits and capacity as two stacked values separated by a horizontal divider: the remaining count above (bold, `data-testid="donut-center-remaining"`) and the capacity count below (muted, `data-testid="donut-center-capacity"`). Both values MUST use locale-aware thousands separators (e.g. `7,331` and `7,560`). Compact-format abbreviation (e.g. `7.33k`) MUST NOT be used in the donut center for these panels.

The primary donut title MUST read `5-Hour Credits`. The secondary donut title MUST read `Weekly Credits`.

#### Scenario: Dashboard donut shows stacked remaining and capacity

- **WHEN** the dashboard renders a usage donut with `remaining=7331` and `total=7560`
- **THEN** the donut title reads `5-Hour Credits` or `Weekly Credits`
- **AND** the center renders `7,331` in the remaining element and `7,560` in the capacity element
- **AND** a divider separates the two values

## ADDED Requirements

### Requirement: Dashboard account summaries sorted by primary capacity

The dashboard overview API MUST return account summaries sorted by `capacity_credits_primary` in descending order so the highest-capacity accounts appear first. Accounts with no primary capacity MUST sort after accounts that have one.

#### Scenario: Accounts ordered by primary capacity

- **WHEN** the dashboard overview response includes multiple accounts with different `capacity_credits_primary` values
- **THEN** accounts are ordered from highest to lowest primary capacity

#### Scenario: Accounts without primary capacity sort last

- **WHEN** an account has `capacity_credits_primary` of `null` or `0`
- **THEN** that account appears after accounts with a positive primary capacity

### Requirement: Account card row height is 11.5rem

The dashboard account card viewport MUST use 11.5rem per visible row.

#### Scenario: Account card max height

- **WHEN** the account cards container renders with `ACCOUNT_CARD_VISIBLE_ROWS=2`
- **THEN** the container `maxHeight` is `calc(2 * 11.5rem + 1rem)`

### Requirement: Weekly credits pace header uses flex-start alignment

The weekly credits pace card header MUST align the title and gauge icon to the flex start, not vertically centered.

#### Scenario: Header alignment

- **WHEN** the weekly credits pace card renders
- **THEN** the header row uses `justify-between` without `items-center`
