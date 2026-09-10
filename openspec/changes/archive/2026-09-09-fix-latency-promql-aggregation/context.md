# Aggregate latency PromQL context

## Purpose

Classic histogram quantiles require bucket aggregation while retaining `le`.

## Decision

Grafana already selects namespace/job, so matching five-minute rates are summed
by `le` before quantile. The alert preserves namespace/job scope by summing by
namespace, job, and `le`.

## Constraints

- Preserve windows, quantiles, thresholds, duration, and instrumentation.
- Remove method/path/replica/scrape labels before quantile.
- Cover all four dashboard expressions and the named alert.

## Example

Multiple request paths and replicas under one selected dashboard scope produce
one p50, p95, and p99 instead of indistinguishable duplicate series.
