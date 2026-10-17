## ADDED Requirements

### Requirement: Observed monthly quota is independent of plan capacity
The system SHALL normalize a lone primary quota of 40320 through 46080 minutes inclusive as monthly, whether its secondary window is absent or a zero-duration placeholder. Poll and live ingestion SHALL apply the same classification. Account summaries SHALL retain observed monthly duration and remaining percentage regardless of plan credit capacity, and SHALL leave unknown credit estimates null. A newer valid short or weekly quota sample SHALL supersede an older monthly sample for a plan without monthly credit capacity.

#### Scenario: Team monthly observation
- **WHEN** a Team account reports a lone primary quota of 43200 or 43800 minutes with 96 percent used
- **THEN** its summary exposes 4 percent monthly remaining and the observed duration
- **AND** it exposes no synthetic primary or secondary quota and no invented monthly credits

#### Scenario: Zero-duration secondary placeholder
- **WHEN** a monthly primary observation includes a secondary window with zero duration
- **THEN** it is normalized to monthly only

#### Scenario: Paid plan upgrade
- **WHEN** a plan without monthly credit capacity has short or weekly quota observed after its monthly quota
- **THEN** the summary uses the newer short or weekly quota and omits the stale monthly quota

#### Scenario: Ordinary and out-of-band windows
- **WHEN** primary duration is 300, 10080, 40319 or 46081 minutes, or a positive-duration secondary exists
- **THEN** monthly-only normalization does not replace those windows

## RENAMED Requirements

- FROM: `### Requirement: Free-account quota surfaces are monthly-only`
- TO: `### Requirement: Monthly-only account quota surfaces`
