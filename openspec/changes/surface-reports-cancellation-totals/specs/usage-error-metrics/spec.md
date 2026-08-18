## MODIFIED Requirements

### Requirement: Cancelled counts surface alongside error counts

Metric surfaces that expose an error count MUST also expose the window's
cancelled count as an additive field: the dashboard overview metrics
(`cancelledCount`), the usage summary metrics (`cancelled7d`), the raw Reports
backend daily rows (`cancelled_count`) and summary (`total_cancelled`), and the
fleet pressure metrics (`cancelledCount`). The dashboard overview cancelled total
MUST be sourced from the demand quarter rollup (status grain) for the folded
segment plus the raw tail, so it stays accurate across history already folded
without the hourly `cancelled_count` measure. The dashboard frontend MUST
preserve `cancelledCount` when parsing the overview response.

The Reports dashboard API MUST serialize the raw backend `total_cancelled` and
`cancelled_count` fields as `summary.totalCancelled` and
`daily[].cancelledCount`, respectively. The Reports frontend MUST preserve
those parsed camelCase values from the reports response. A daily row
synthesized to fill a missing date in the selected range MUST set
`cancelledCount` to `0`. The Reports summary and daily table MUST visibly show
the cancellation values with localized labels, and the Reports CSV export MUST
include a localized cancellation header and each daily row's cancellation
value. Adding cancellation presentation MUST NOT change the parsed, visible, or
exported request and error values.

#### Scenario: Dashboard overview reports the status breakdown

- **GIVEN** a window containing 1 successful, 2 cancelled, and 1 error rows
  that are partially folded into the rollups
- **WHEN** the dashboard overview metrics are computed
- **THEN** the metrics expose `requests=4`, `errorCount=1`, and
  `cancelledCount=2`

#### Scenario: Dashboard overview preserves the status breakdown

- **GIVEN** the dashboard overview API returns `requests=4`, `errorCount=1`,
  and `cancelledCount=2`
- **WHEN** the frontend parses the overview response
- **THEN** the parsed metrics expose all three values unchanged

#### Scenario: Reports preserve and display cancellation values

- **GIVEN** a reports response whose summary has `totalRequests=4`,
  `totalCancelled=2`, and `totalErrors=1` and whose frontend daily row has
  `requests=4`, `cancelledCount=2`, and `errorCount=1`
- **WHEN** the Reports frontend parses and displays the response
- **THEN** the parsed summary has `totalCancelled=2` and the parsed daily row
  has `cancelledCount=2`
- **AND** the localized summary visibly shows requests `4`, cancellations `2`,
  and errors `1`
- **AND** the localized daily table visibly shows requests `4`, cancellations
  `2`, and errors `1`

#### Scenario: Reports zero-fill cancellations for a missing date

- **GIVEN** a selected report range containing a date absent from the reports
  response
- **WHEN** the Reports frontend synthesizes the daily row for that date
- **THEN** the synthesized row has `cancelledCount=0`
- **AND** the daily table visibly shows cancellation value `0` for that row

#### Scenario: Reports CSV exports localized cancellation values

- **GIVEN** parsed report rows with cancellation values `2` and `0`
- **WHEN** a user exports the report while a supported locale is active
- **THEN** the CSV contains the locale's cancellation header and the values `2`
  and `0` in that column
- **AND** the exported request and error headers and values remain present and
  unchanged
