## ADDED Requirements

### Requirement: API sidebar shows pooled credit bars

The APIs page left sidebar SHALL render pooled credit bars on each API key list item. Each bar SHALL display a label, percentage, and colored progress bar using the same `MiniQuotaBar` component as the Accounts sidebar.

Labels SHALL be "Pooled 5h" for the primary window and "Pooled Weekly" for the secondary window. No reset countdown text SHALL be shown.

When `pooledCapacityCreditsPrimary > 0` and `pooledRemainingPercentPrimary` is not null, the "Pooled 5h" bar SHALL be visible. Otherwise it SHALL be hidden. The "Pooled Weekly" bar SHALL be visible when `pooledRemainingPercentSecondary` is not null.

When both bars are visible, they SHALL be laid out in a 2-column grid. When only one bar is visible, it SHALL use a 1-column layout.

When API key limit rules exist, the sidebar SHALL also render the legacy limit progress bar below the pooled bars with an "API Limit" label and percentage value so it remains clearly distinct from the pooled-account bars.

#### Scenario: Both pooled bars visible

- **WHEN** an API key has both primary and secondary pooled credit data
- **THEN** the sidebar item shows "Pooled 5h" and "Pooled Weekly" bars in a 2-column grid

#### Scenario: Primary bar hidden for free-tier accounts

- **WHEN** an API key's pooled primary capacity is 0
- **THEN** only the "Pooled Weekly" bar is shown in a 1-column layout

#### Scenario: No credit data hides bars

- **WHEN** an API key has no pooled credit data
- **THEN** no credit bars are rendered on that list item

#### Scenario: API limit bar is labeled distinctly

- **WHEN** an API key has configured limit rules
- **THEN** the sidebar renders the legacy limit bar with an "API Limit" label below the pooled bars
