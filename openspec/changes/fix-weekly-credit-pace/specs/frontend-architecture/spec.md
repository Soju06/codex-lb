## MODIFIED Requirements

### Requirement: Dashboard weekly credits pace

The dashboard SHALL show weekly quota pace when account weekly capacity credits, remaining credits, reset time, and window length are available. The pace calculation MUST use credit totals rather than averaging per-account percentages, because weekly ChatGPT quota credits are not the same unit as raw request tokens. The dashboard MUST prefer the backend-provided `weeklyCreditPace` object from `GET /api/dashboard/overview` when present, and MAY fall back to a local calculation only for older responses that do not include that field.

#### Scenario: Weekly credits pace uses account reset deadlines

- **WHEN** multiple accounts have weekly quota data with different `resetAtSecondary` values
- **THEN** the system computes each account's expected remaining weekly credits from that account's own reset time and window length before summing totals

#### Scenario: Weekly credits pace excludes inactive or stale usage rows

- **WHEN** an account is not active or its latest weekly usage sample is older than the freshness window derived from the usage refresh interval
- **THEN** the account is not included in weekly pace totals or forecasts
- **AND** the response reports the excluded stale account count separately from the included account count

#### Scenario: Current schedule gap is separate from forecast shortfall

- **WHEN** actual remaining weekly credits are lower than scheduled remaining weekly credits
- **THEN** the response reports `scheduleGapCredits` for the current deficit against the linear schedule
- **AND** the response reports `projectedShortfallCredits` only for a future shortfall forecast based on recent burn
- **AND** the dashboard labels the two concepts separately

#### Scenario: Forecast burn uses recent weekly usage slope

- **WHEN** an account has high cumulative weekly usage from earlier in the window but no recent increase in weekly used percent
- **THEN** the projected shortfall forecast is based on the recent slope and does not assume the earlier full-window average continues

#### Scenario: Near-reset depletion is not a false alarm

- **WHEN** an account has consumed 99% of its weekly quota and 99% of its weekly window has elapsed
- **THEN** the weekly pace treats that account as on pace rather than over plan

#### Scenario: Missing weekly credit data is omitted

- **WHEN** an account is missing weekly capacity credits, remaining credits, reset time, or window length
- **THEN** that account is omitted from weekly pace calculation

#### Scenario: No valid weekly credit data hides pace

- **WHEN** no account has complete, active, fresh weekly credits pace data
- **THEN** the dashboard does not render a fake weekly pace value
