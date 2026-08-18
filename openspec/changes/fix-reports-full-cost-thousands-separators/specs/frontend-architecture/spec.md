## ADDED Requirements

### Requirement: Reports full-value USD displays use grouped currency formatting

The dashboard SHALL render non-compact USD Cost values on `/reports` through the shared currency formatter so values at or above one thousand include locale-appropriate grouping separators and exactly two fractional digits. This requirement applies to the Total Cost summary value, its average-cost-per-day subtitle, Daily Breakdown Cost cells, and Cost by Day axis and tooltip values.

#### Scenario: Summary and daily Cost values exceed one thousand USD

- **WHEN** an authenticated operator views `/reports` data whose full-value Cost amount is `1400`
- **THEN** the Total Cost summary value renders `$1,400.00`
- **AND** the average-cost-per-day subtitle, when its amount is `1400`, renders `$1,400.00`
- **AND** a Daily Breakdown Cost cell whose amount is `1400` renders `$1,400.00`
- **AND** a Cost by Day axis tick whose amount is `1400` renders `$1,400.00`
- **AND** a Cost by Day tooltip whose amount is `1400` renders `$1,400.00`

#### Scenario: Intentionally compact Cost visualization remains compact

- **WHEN** an authenticated operator views a constrained Reports distribution visualization whose Cost label uses compact notation
- **THEN** that visualization may continue to render a compact label such as `$1.4K`
- **AND** the full-value Cost displays remain grouped currency values
