## ADDED Requirements

### Requirement: APIs tab shows a 7-day account-cost donut for selected API keys

When the selected API key's 7-day usage payload contains one or more `accountCosts[]` items, the APIs tab detail panel SHALL render an account-cost donut card beside the usage trend card. On large screens, the split layout SHALL use a 25:75 width ratio with the donut on the left and the trend on the right.

The donut card SHALL include a title and subtitle, SHALL show the 7-day total cost in the donut center, and SHALL render a legend beneath the donut.

#### Scenario: Donut renders beside the usage trend
- **WHEN** a selected API key has 7-day account-cost data and trend data
- **THEN** the detail panel renders the account-cost donut card to the left of the trend card
- **AND** the large-screen grid uses a 25:75 width split

#### Scenario: Donut is omitted when no account-cost buckets exist
- **WHEN** the selected API key's `usage-7d.accountCosts[]` array is empty
- **THEN** the APIs tab does not render the account-cost donut card

### Requirement: APIs tab account-cost donut uses existing account labels and privacy rules

The donut legend SHALL use the account label derived from the existing payload fields: `Deleted Account` for `isDeleted: true`, otherwise the account `email` when present, otherwise `Unknown Account`. Non-deleted account labels MUST respect the hide-account-info privacy setting used elsewhere in the dashboard.

The legend SHALL show each visible bucket's 7-day cost, SHALL display at most four account rows, and SHALL indicate when additional rows exist beyond the first four.

#### Scenario: Deleted account label is explicit
- **WHEN** an `accountCosts[]` item has `isDeleted: true`
- **THEN** the legend label is `Deleted Account`

#### Scenario: Privacy hiding applies to non-deleted account labels
- **WHEN** the hide-account-info setting is enabled
- **AND** a visible donut legend row represents a non-deleted account label
- **THEN** the label text is privacy-blurred

#### Scenario: Legend is capped at four visible rows
- **WHEN** more than four account-cost buckets are present
- **THEN** the donut legend renders only the first four rows
- **AND** the card indicates how many additional rows remain hidden

### Requirement: APIs tab account-cost donut follows the dashboard donut visual system

The account-cost donut SHALL use the same sizing, palette generation, reduced-motion behavior, and gray consumed/deleted color treatment as the dashboard donut visual system.

#### Scenario: Deleted-account slice uses the consumed gray color
- **WHEN** the donut renders a deleted-account bucket
- **THEN** that bucket uses the same gray color family used by the dashboard donut's consumed or used segment

### Requirement: APIs tab usage trend control layout is compact in the split view

The APIs tab usage trend card SHALL keep its heading and subtitle, SHALL render the accumulated toggle above the Tokens/Cost legend, and SHALL reduce the chart right margin to fit the split layout.

#### Scenario: Tokens and cost legend sits below accumulated toggle
- **WHEN** the usage trend card renders
- **THEN** the accumulated toggle appears above the Tokens/Cost legend

#### Scenario: Usage trend uses compact right margin
- **WHEN** the usage trend chart renders in the split APIs-tab layout
- **THEN** the chart right margin is reduced from the previous wider layout to a compact right margin
