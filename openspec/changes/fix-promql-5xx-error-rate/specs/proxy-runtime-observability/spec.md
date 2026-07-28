## ADDED Requirements

### Requirement: Shipped high-error-rate alert uses aggregate request share

The shipped `CodexLBHighErrorRate` alert MUST calculate, independently for each
namespace and job, the sum of five-minute 5xx request rates divided by the sum
of all five-minute request rates. Method, path, status, instance, replica, and
other non-scope labels MUST be aggregated before division. The alert MUST
compare the aggregate ratio to 0.05 and MUST require it to remain above that
threshold for five minutes.

#### Scenario: Mixed success and error series produce their aggregate share

- **GIVEN** one namespace and job have positive 2xx and 5xx request rates
- **WHEN** the high-error-rate alert expression is evaluated
- **THEN** the ratio equals the sum of 5xx request rates divided by the sum of
  all request rates
- **AND** the ratio is not 1 unless all requests in that group are 5xx

#### Scenario: Alert groups remain isolated

- **GIVEN** request series exist for more than one namespace or job
- **WHEN** the high-error-rate alert expression is evaluated
- **THEN** each namespace and job pair has an independent aggregate ratio
- **AND** traffic from one pair is not included in another pair

#### Scenario: Threshold and duration apply to the aggregate ratio

- **GIVEN** one namespace and job have an aggregate 5xx share above 0.05
- **WHEN** that aggregate share remains above 0.05 for five minutes
- **THEN** `CodexLBHighErrorRate` fires for that namespace and job pair

### Requirement: Bundled Grafana 5xx stat uses selected aggregate request share

The bundled Grafana `Error Rate (5xx)` stat MUST apply the selected namespace
and job filters to both operands, aggregate all remaining request-series labels
before division, and display the resulting 5xx share as one value. When the
selected total request rate is positive but no matching 5xx series exists, the
stat MUST display 0%.

#### Scenario: Selected mixed traffic produces one aggregate value

- **GIVEN** the selected namespace and job have positive 2xx and 5xx request
  rates across one or more request or replica label combinations
- **WHEN** the Grafana error-rate stat is evaluated
- **THEN** it displays the sum of selected 5xx request rates divided by the sum
  of all selected request rates

#### Scenario: Dashboard selection filters both operands

- **GIVEN** request series exist inside and outside the selected namespace and
  job
- **WHEN** the Grafana error-rate stat is evaluated
- **THEN** both the 5xx numerator and total denominator exclude traffic outside
  the selected namespace and job

#### Scenario: Success-only traffic displays zero

- **GIVEN** the selected scope has a positive successful-request rate
- **AND** no matching 5xx series exists
- **WHEN** the Grafana error-rate stat is evaluated
- **THEN** the stat displays 0%
