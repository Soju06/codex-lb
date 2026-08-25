## ADDED Requirements

### Requirement: Dashboard refresh cadence is operator-selectable

The dashboard MUST let the operator select a 5, 15, 30, or 60 second refresh
cadence. The selection MUST persist locally, default to 15 seconds, and apply to
both overview and projection queries without enabling background-tab polling.
Failed refreshes MUST retain the last successful query data.

#### Scenario: Refresh cadence updates live dashboard queries

- **GIVEN** the operator selects a 5-second dashboard refresh cadence
- **WHEN** the dashboard overview and projection queries are active
- **THEN** both queries poll every 5 seconds
- **AND** the preference survives a page reload
- **AND** a failed poll does not clear the last successful data

### Requirement: Weekly burn smoothing is independent of sample cadence

The weekly pace forecast MUST smooth recent consumption with a time-based EWMA
whose half-life is the configured pace smoothing window. The filter MUST start
from a zero-rate baseline for a new quota segment, MUST reset when the upstream
quota window changes, and MUST reject non-finite samples. Equivalent usage over
the same wall-clock interval SHOULD yield equivalent burn estimates when the
refresh cadence changes.

#### Scenario: Quantized provider percentages do not create a burn-rate pulse

- **GIVEN** recent weekly samples hold steady and then increase by one provider percentage point
- **WHEN** the weekly pace forecast is calculated
- **THEN** the one-point step is damped across the configured smoothing window
- **AND** the forecast does not report the full adjacent-sample spike as the sustained burn rate

#### Scenario: Refresh cadence does not redefine smoothing

- **GIVEN** two histories describe the same percentage change over the same wall-clock interval at different sample cadences
- **WHEN** both histories use the same pace smoothing window
- **THEN** their smoothed burn estimates are materially equivalent
