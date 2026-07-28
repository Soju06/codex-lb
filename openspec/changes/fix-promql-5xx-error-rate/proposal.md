## Why

The shipped Prometheus alert and Grafana stat divide per-status request series
without first combining the status dimension. A 5xx series can therefore divide
by the identical 5xx series in the denominator and report approximately 100%
instead of the actual share of failed requests.

## What Changes

- Calculate the 5xx share from separately aggregated error and total request
  rates.
- Keep alert instances isolated by Kubernetes namespace and Prometheus job while
  aggregating request, status, and replica dimensions.
- Make the Grafana stat honor its namespace and job selections and display 0%
  when successful traffic exists before any 5xx series has been created.
- Add focused configuration regressions for both distributed monitoring
  artifacts.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `proxy-runtime-observability`: Defines the operator-facing Prometheus alert
  and Grafana dashboard contracts for aggregate HTTP 5xx error share.

## Impact

The change is limited to the Helm chart's `PrometheusRule`, bundled Grafana
dashboard, their configuration tests, and this OpenSpec change. It adds no
setting, dependency, API, schema, migration, navigation item, or production
rollout action.
