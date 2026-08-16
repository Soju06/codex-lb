## Context

`codex_lb_requests_total` is split across request labels such as `status`,
`method`, and `path`, plus scrape-time labels such as `namespace`, `job`, and
replica identity. Prometheus binary operators match vectors by label set. The
shipped expressions divide before aggregation, so a 5xx numerator series can
match the identical 5xx series in the denominator and produce approximately
100% even when most requests succeed.

The Prometheus alert must preserve independent alert instances for each
Kubernetes namespace and Prometheus job. The Grafana stat must instead produce
one aggregate value for the namespace and job selected by the dashboard
operator.

## Goals / Non-Goals

**Goals:**

- Calculate the 5xx share from separately aggregated error and total request
  rates over the same five-minute window.
- Preserve the alert threshold, duration, and isolation by namespace and job.
- Apply the dashboard's namespace and job selections to both operands.
- Show 0% in Grafana when selected traffic is positive but no 5xx series
  exists.

**Non-Goals:**

- Change the request metric, its labels, the five-minute window, the alert
  threshold, or the alert duration.
- Define an error share when selected total traffic is absent or has zero
  rate.
- Add settings, dependencies, runtime behavior, dashboards, or deployment
  actions beyond the two existing monitoring artifacts.

## Decisions

### Aggregate each operand before division

The alert uses `sum by (namespace, job)` independently for the 5xx and total
rates before dividing them. This removes status, request, and replica
dimensions while retaining the two labels that define an alert instance.
Vector matching then compares one error-rate series with one total-rate series
for the same namespace and job.

Using a binary `ignoring(status)` modifier was rejected because it would leave
other request and replica dimensions in the vectors and could create
many-to-one matching or duplicated denominators.

### Produce one symmetrically filtered Grafana value

The dashboard applies the selected namespace and job matchers to both rate
selectors, sums each operand without grouping labels, and divides the resulting
single-series vectors. The numerator alone falls back with `or vector(0)`.
Consequently, positive selected traffic with no 5xx series evaluates to 0%,
while an absent denominator remains no-data and a zero-rate denominator
remains undefined instead of fabricating a healthy value. The job selector is
a regex matcher so Grafana's All value continues to work; the single-select
namespace remains an equality matcher.

Clamping or defaulting the denominator was rejected because an error share is
not defined when there is no selected traffic.

### Lock the distributed artifact contract in focused tests

Configuration tests extract the named alert expression and the named Grafana
panel target, then compare their normalized or exact PromQL. This catches a
future return to divide-before-aggregate semantics as well as asymmetric
dashboard filters. A deterministic isolated Prometheus and Grafana fixture
provides direct before/after rendering evidence for the shipped dashboard.

## Risks / Trade-offs

- **No or zero selected traffic can render no-data or an undefined value.**
  This is deliberate because the ratio has no meaningful denominator; the
  change does not alter Grafana's existing `lastNotNull` reduction behavior,
  which can retain an older value over the selected time range.
- **An absent 5xx alert series produces no alert instance.** This is the
  correct result when no 5xx requests exist; Grafana alone needs an explicit
  zero for its visible stat.
- **The regression tests intentionally bind exact query structure.** This is
  appropriate for shipped configuration artifacts whose vector semantics can
  change through small textual edits; equivalent rewrites must update the
  contract test deliberately.

No data migration is required. Rollback consists of restoring the prior chart
artifacts; request metrics and stored data are unchanged.
