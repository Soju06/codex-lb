## ADDED Requirements

### Requirement: APIs tab shows a 7-day account-cost donut for selected API keys

When the selected API key's 7-day usage payload contains one or more `accountCosts[]` items, the APIs tab detail panel SHALL render the account-cost donut section and usage-trend section inside a single shared card. On large screens, the split layout SHALL use a 25:75 width ratio with the donut on the left, the trend on the right, and a vertical separator between them.

The donut section SHALL include a title and subtitle, SHALL show the 7-day total cost in the donut center, SHALL not render a separate `Total $...` summary in the section header, and SHALL render the legend below the donut.

#### Scenario: Donut renders inside the shared usage card
- **WHEN** a selected API key has 7-day account-cost data and trend data
- **THEN** the detail panel renders the account-cost donut section to the left of the trend section inside one shared card
- **AND** the large-screen layout uses a 25:75 split with a vertical separator between the sections

#### Scenario: Donut is omitted when no account-cost buckets exist
- **WHEN** the selected API key's `usage-7d.accountCosts[]` array is empty
- **THEN** the APIs tab does not render the account-cost donut card

### Requirement: APIs tab account-cost donut uses existing account labels and privacy rules

The donut legend SHALL use the account label derived from the existing payload fields: `Deleted Account` for `isDeleted: true`, otherwise the account `email` when present, otherwise `Unknown Account`. Non-deleted account labels MUST respect the hide-account-info privacy setting used elsewhere in the dashboard.

The legend SHALL show each visible bucket's 7-day cost, SHALL coordinate hover highlighting with the matching pie slice, and SHALL use the same vertically scrollable five-row viewport pattern as the dashboard donuts when more rows exist than fit without scrolling.

#### Scenario: Deleted account label is explicit
- **WHEN** an `accountCosts[]` item has `isDeleted: true`
- **THEN** the legend label is `Deleted Account`

#### Scenario: Privacy hiding applies to non-deleted account labels
- **WHEN** the hide-account-info setting is enabled
- **AND** a visible donut legend row represents a non-deleted account label
- **THEN** the label text is privacy-blurred

#### Scenario: Legend scroll viewport matches dashboard donuts
- **WHEN** more than five account-cost buckets are present
- **THEN** the donut legend keeps all rows available
- **AND** the visible legend viewport shows five rows before scrolling

### Requirement: APIs tab account-cost donut follows the dashboard donut visual system

The account-cost donut SHALL use the same sizing, palette generation, reduced-motion behavior, hover-linked legend highlighting, and gray consumed/deleted color treatment as the dashboard donut visual system.

#### Scenario: Deleted-account slice uses the consumed gray color
- **WHEN** the donut renders a deleted-account bucket
- **THEN** that bucket uses the same gray color family used by the dashboard donut's consumed or used segment

### Requirement: APIs tab usage trend control layout is compact in the split view

The APIs tab usage trend card SHALL keep its heading and subtitle, SHALL align the accumulated toggle and Tokens/Cost legend to the right side of the heading block on larger screens, and SHALL reduce the chart right margin to fit the split layout.

#### Scenario: Usage trend controls align with the heading row
- **WHEN** the usage trend card renders
- **THEN** the Tokens/Cost legend appears to the right of the heading block on larger screens
- **AND** the accumulated toggle remains in the same right-side controls group

#### Scenario: Usage trend uses compact right margin
- **WHEN** the usage trend chart renders in the split APIs-tab layout
- **THEN** the chart right margin is reduced from the previous wider layout to a compact right margin
