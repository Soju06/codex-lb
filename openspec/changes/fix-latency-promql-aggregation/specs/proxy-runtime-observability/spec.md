## ADDED Requirements

### Requirement: Bundled Grafana latency quantiles aggregate selected buckets

Every bundled Grafana request/upstream `histogram_quantile` MUST apply selected
namespace/job filters and sum five-minute bucket rates by `le` before quantile.
Residual method, path, instance, pod, replica, and scrape labels MUST NOT create
additional quantile series.

#### Scenario: Selected latency produces one series per quantile

- **GIVEN** matching buckets span methods, paths, and scrape targets
- **WHEN** bundled request p50/p95/p99 or upstream p99 evaluates
- **THEN** matching rates are summed by `le` first
- **AND** one selected-scope series remains per quantile

### Requirement: High-latency alert aggregates by operational scope

`CodexLBHighLatency` MUST calculate p99 from request buckets summed by
namespace, job, and `le`. It MUST retain the ten-second threshold and
five-minute duration.

#### Scenario: Residual labels produce one alert value per scope

- **GIVEN** one namespace/job has buckets across methods, paths, and replicas
- **WHEN** the alert evaluates
- **THEN** one aggregate p99 remains for that namespace/job
