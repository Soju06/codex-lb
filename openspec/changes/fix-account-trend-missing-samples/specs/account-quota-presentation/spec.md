## ADDED Requirements

### Requirement: Faithful account trend series
The account trend chart SHALL preserve every distinct observation instant and SHALL linearly interpolate missing window samples between observations using elapsed time and SHALL hold the last observed value after the final observation. Values before the first observation SHALL remain unknown; actual zero observations SHALL be preserved. Legends SHALL list only available series. Monthly-account long-window tooltips SHALL use monthly labels.

#### Scenario: Different observation timestamps
- **GIVEN** a primary observation and a monthly observation have different timestamps
- **WHEN** the chart combines the series
- **THEN** both observations SHALL be retained and each missing counterpart SHALL interpolate between surrounding observations, retain its last observed value if no later observation exists, or remain unknown if no earlier observation exists.

#### Scenario: Equivalent instants with different UTC offsets
- **GIVEN** quota observations and a scheduled value refer to the same instant using different UTC offsets
- **WHEN** the chart combines the series
- **THEN** it SHALL show one point at that instant with each observed and scheduled value available.

#### Scenario: Monthly-only trend
- **GIVEN** only monthly observations exist
- **THEN** the chart SHALL show Monthly without a 5-hour legend and SHALL label its long-window tooltip Monthly.

### Requirement: Account-scoped quota presentation state
Smoothed quota values and known-window state SHALL reset when the selected account changes.

#### Scenario: Switching quota shapes
- **GIVEN** a dual-window account was selected
- **WHEN** the operator selects a different monthly-only account
- **THEN** only the new account's monthly quota SHALL be shown, without retained 5-hour or weekly values.
