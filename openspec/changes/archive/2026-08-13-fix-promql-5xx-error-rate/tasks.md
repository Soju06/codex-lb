## 1. Monitoring contract

- [x] 1.1 Add the aggregate 5xx-share requirements and design decisions to
  OpenSpec.
- [x] 1.2 Update the Prometheus alert to aggregate request series before
  division while retaining namespace and job alert groups.
- [x] 1.3 Update the Grafana stat to aggregate before division, apply symmetric
  namespace and job filters, and handle an absent 5xx series.

## 2. Regression and evidence

- [x] 2.1 Add focused configuration regressions for the shipped alert and
  dashboard queries.
- [x] 2.2 Capture and independently privacy-review deterministic before/after
  rendering from an isolated local Prometheus and Grafana fixture.
- [x] 2.3 Run focused tests, formatting checks, and strict OpenSpec validation.
