## ADDED Requirements

### Requirement: Daily group usage series

The group response SHALL include, for every member, a `dailyUsage` array containing one entry per UTC calendar date intersecting `[from, until)`. Each entry SHALL contain the date, total tokens, and USD cost for that member. The service MUST include zero-valued entries for dates without usage, preserve the rolling window's partial boundary semantics, and calculate member summary token and cost totals from the same daily values. Retained hourly rollups and raw request windows MUST produce equivalent daily values without double counting.

#### Scenario: Render a dense rolling series

- **WHEN** a grouped key requests usage across a rolling thirty-day window
- **THEN** every member receives the same ordered UTC date sequence
- **AND** unused dates contain zero tokens and zero cost
- **AND** the first or last date may contain only the portion inside the rolling window

#### Scenario: Preserve daily values across aggregation boundaries

- **GIVEN** requests span UTC midnight and some older requests have been folded into hourly rollups
- **WHEN** the group usage endpoint reads the window before and after folding
- **THEN** each request contributes to the UTC date of its request timestamp exactly once
- **AND** the member summary totals equal the sums of its daily series

### Requirement: Interactive daily group chart

The Group keys tab SHALL render a responsive, accessible daily chart after group totals. It MUST provide Tokens and Cost (USD) metric controls, one distinguishable line per member, per-member visibility controls, exact-value tooltips, and an accessible tabular view of the currently selected metric. Toggling metrics or members MUST use the loaded response without another network request. The chart and table MUST remain usable at a 390-pixel viewport without page-level horizontal overflow, and unmounting, disconnecting, refreshing, or receiving a group 401 MUST clear the chart with the rest of group state.

#### Scenario: Compare metrics and members

- **WHEN** a user switches from Tokens to Cost or hides a member
- **THEN** axes, tooltip values, table values, and visible lines update to that selection
- **AND** the other members' values remain available without refetching

#### Scenario: Use the accessible daily table

- **WHEN** a user expands the daily data disclosure
- **THEN** a labeled table lists each UTC date and the selected metric for every visible member
- **AND** the table remains keyboard operable and scrollable within the chart card on narrow screens
