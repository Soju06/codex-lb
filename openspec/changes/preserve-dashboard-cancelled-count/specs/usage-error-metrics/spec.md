## MODIFIED Requirements

### Requirement: Cancelled counts surface alongside error counts

Metric surfaces that expose an error count MUST also expose the window's
cancelled count as an additive field: the dashboard overview metrics
(`cancelledCount`), the usage summary metrics (`cancelled7d`), the reports
daily rows (`cancelled_count`) and summary (`total_cancelled`), and the fleet
pressure metrics (`cancelledCount`). The dashboard overview cancelled total
MUST be sourced from the demand quarter rollup (status grain) for the folded
segment plus the raw tail, so it stays accurate across history already folded
without the hourly `cancelled_count` measure. The dashboard frontend MUST
preserve `cancelledCount` when parsing the overview response.

#### Scenario: Dashboard overview preserves the status breakdown

- **GIVEN** the dashboard overview API returns `requests=4`, `errorCount=1`,
  and `cancelledCount=2`
- **WHEN** the frontend parses the overview response
- **THEN** the parsed metrics expose all three values unchanged
