# frontend-architecture delta

## MODIFIED Requirements

### Requirement: Dashboard weekly credits pace

The dashboard SHALL show weekly quota runway when account weekly capacity credits, remaining credits, reset time, and window length are available. The card MUST present, in priority order: fleet headroom (percent and credits), depletion ETA at the recent burn rate, the next reset relief (arrival time and credits returned), a survives-to-relief verdict, and per-API-key burn attribution for the trailing two hours. The runway calculation MUST use credit totals rather than averaging per-account percentages. The dashboard MUST render the card immediately from the `weeklyCreditPace` object in `GET /api/dashboard/overview` without waiting for any other request, and MAY refine it when the projections payload arrives. Card status MUST derive from the relief verdict (`safe`, `tight`, `runs_dry`) rather than from deviation against a linear schedule. Linear-schedule pace fields SHALL remain populated in the response for one release for wire compatibility.

#### Scenario: Weekly credits pace uses account reset deadlines

- **WHEN** multiple accounts have weekly quota data with different `resetAtSecondary` values
- **THEN** the system computes depletion, relief, and expected remaining weekly credits from each account's own reset time and window length before summing fleet totals

#### Scenario: Weekly credits pace excludes hard-blocked or stale usage rows

- **WHEN** an account is `reauth_required`, paused, deactivated, missing from the account table, or its latest weekly usage sample is older than the freshness window derived from the usage refresh interval
- **THEN** the account is not included in weekly runway totals or forecasts
- **AND** the response reports the excluded stale account count separately from the included account count

#### Scenario: Exhausted accounts still count in weekly credits pace

- **WHEN** an account is `rate_limited` or `quota_exceeded`
- **AND** it has complete, fresh weekly capacity, remaining credits, reset time, and window length
- **THEN** the account is included in weekly runway totals and forecasts

#### Scenario: Current schedule gap is separate from forecast shortfall

- **WHEN** actual remaining weekly credits are lower than scheduled remaining weekly credits
- **THEN** the response reports `scheduleGapCredits` for the current deficit against the linear schedule
- **AND** the response reports `projectedShortfallCredits` only for a future shortfall forecast based on recent burn
- **AND** any surface that presents the linear-schedule deficit describes it as over planned usage, fewer credits remaining than scheduled, or equivalent over-consumption wording rather than "behind schedule"

#### Scenario: Displayed pace gap uses configured smoothing

- **GIVEN** the weekly pace gap smoothing window is configured
- **WHEN** recent weekly usage samples are available for the current weekly reset/window segment
- **THEN** the response includes `smoothedDeltaPercent`, `smoothedScheduleGapCredits`, and `paceGapSmoothingMinutes`
- **AND** `actualUsedPercent` remains the live current value

#### Scenario: Weekly pace smoothing resets with quota window

- **GIVEN** a smoothing time window contains samples from before and after a weekly quota reset
- **WHEN** the latest sample belongs to the new reset/window segment
- **THEN** the smoothed pace gap excludes the samples from the previous reset/window segment

#### Scenario: Forecast burn uses recent weekly usage slope

- **WHEN** an account has high cumulative weekly usage from earlier in the window but no recent increase in weekly used percent
- **THEN** the depletion forecast is based on the recent slope and does not assume the earlier full-window average continues

#### Scenario: Near-reset depletion is not a false alarm

- **WHEN** an account has consumed 99% of its weekly quota and 99% of its weekly window has elapsed
- **THEN** the runway verdict treats that account's imminent reset as relief rather than reporting it as over plan

#### Scenario: Missing weekly credit data is omitted

- **WHEN** an account is missing weekly capacity credits, remaining credits, reset time, or window length
- **THEN** that account is omitted from weekly runway calculation

#### Scenario: No valid weekly credit data hides pace

- **WHEN** no account has complete, fresh weekly credits pace data for an `active`, `rate_limited`, or `quota_exceeded` account
- **THEN** the dashboard does not render a fake weekly runway value

#### Scenario: Relief falls back to the full fleet when no account is near exhaustion

- **WHEN** no included account is at or above 95 percent weekly usage
- **THEN** the next relief time is the soonest reset among all included accounts
- **AND** the relief credits sum the used credits of included accounts whose reset falls within one hour of that soonest reset

#### Scenario: Verdict reflects whether relief arrives before depletion

- **WHEN** the depletion ETA at the recent burn rate falls before the soonest reset among accounts at or above 95% weekly usage
- **THEN** the response reports `runwayStatus` as `runs_dry`
- **AND** the card presents the depletion time and the missed relief time together

#### Scenario: Surviving to relief is not an incident

- **WHEN** the depletion ETA falls after the soonest relief reset
- **AND** the margin between them is at least 24 hours and fleet headroom is at least 12 percent
- **THEN** the response reports `runwayStatus` as `safe`
- **AND** the card renders without warning emphasis

#### Scenario: Per-key attribution names the burn source

- **WHEN** request logs exist in the trailing two hours
- **THEN** the response lists the top API keys by requests and by billable tokens with request count, billable tokens, and dominant model
- **AND** the card renders them so an operator can identify the consumer without leaving the dashboard

#### Scenario: Saturated fleet labels demand as a floor

- **WHEN** every included account is at or above 99.5 percent weekly usage
- **THEN** demand-derived figures are labeled as at-least floors rather than exact demand

#### Scenario: Add-capacity recommendation is stable and gated

- **WHEN** trailing seven-day fleet demand in quota-weeks exceeds current fleet weekly capacity
- **AND** the runway verdict is `runs_dry` or at least one account is saturated
- **THEN** the response recommends additional Pro accounts computed from the weekly demand surplus
- **AND** the recommendation does not change materially from hour to hour under steady traffic

#### Scenario: Throttle guidance precedes purchase guidance

- **WHEN** the runway verdict is `runs_dry`
- **AND** `throttleToPercent` is available
- **THEN** the card presents throttling to the sustainable rate as the first remedy before any add-capacity suggestion

#### Scenario: Card paints without the projections request

- **WHEN** the overview response has arrived and the projections request is still in flight or failed
- **THEN** the weekly runway card renders its full content from the overview payload with a stable layout footprint
