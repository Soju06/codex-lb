## Why

Bundled Grafana latency panels and the high-latency alert pass raw histogram
bucket rates to `histogram_quantile`. Residual method, path, scrape, pod, and
instance labels therefore produce multiple quantiles instead of the intended
aggregate.

## What Changes

- Sum selected dashboard buckets by `le` before p50/p95/p99.
- Sum alert buckets by namespace, job, and `le` before p99.
- Cover every shipped latency quantile expression.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `proxy-runtime-observability`: aggregate bundled histogram quantiles at their
  intended dashboard and alert scopes.

## Impact

Grafana JSON, PrometheusRule, artifact tests, and OpenSpec only. No metric
schema or instrumentation-label change; only query aggregation changes output
series cardinality. Thresholds, windows, settings, dependencies, and runtime
code remain unchanged.
