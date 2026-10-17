# Account pool metrics

With the existing metrics feature enabled, scrape `/metrics` on the configured
metrics port. These gauges describe the shared account pool:

| Metric | Meaning |
| --- | --- |
| `codex_lb_accounts_total{status="active"}` | Number of accounts with the stored status. The other values are `rate_limited`, `quota_exceeded`, `paused`, `reauth_required`, and `deactivated`. Every status appears, including zeroes. |
| `codex_lb_accounts_available` | Accounts eligible by stored status and reauthentication-token expiry, before request-specific routing filters. |

Accounts pending deletion are excluded from both metrics. A successful scrape
refreshes the committed account snapshot through the existing routing cache,
using one account query. No upstream probe or proxy request is needed. If the
refresh fails, `/metrics` returns HTTP 503; monitor the Prometheus `up` series
alongside account counts.

## What available means

Active accounts count as available. An account with `reauth_required` counts
until its access token has a known expiry at or before the snapshot time. An
unknown or unreadable expiry remains eligible, matching the routing predicate.
An active account with an expired token still counts because ordinary routing
can refresh it. Other stored statuses do not count.

This is a baseline eligibility count. It excludes model support, API-key account
assignments, affinity, live quota calculations, cooldowns, health tiers, stream
caps, and other request-specific constraints. A positive value does not promise
that a particular request can run. Stored quota-related statuses still affect
the count through the status rule above.

For example, two active accounts and one reauthentication-required account with
a future token expiry report availability of 3. If all are occupied by streams,
the value stays 3. Once the reauthentication token expires, the next scrape
reports 2 even if there has been no proxy traffic or database change.

## Alert examples

For a scrape job named `codex-lb`, these expressions identify a pool with no
baseline eligible accounts, fewer than two stored active accounts, and a failed
scrape respectively:

```promql
codex_lb_accounts_available{job="codex-lb"} == 0
codex_lb_accounts_total{job="codex-lb", status="active"} < 2
up{job="codex-lb"} == 0
```

Choose alert durations for your normal pause, reauthentication, and maintenance
windows. Treat a failed scrape separately from a confirmed zero count.

## Workers and replicas

With `PROMETHEUS_MULTIPROC_DIR`, both gauges use the most recent live-worker
observation per series. They have no PID label and do not add together duplicate
views of the same pool. A newer zero replaces an older nonzero observation;
normal worker cleanup removes dead-worker observations.

Scrape each replica directly, as required by the existing replica metrics
contract. Replicas sharing a database describe the same account inventory, so
do not sum these pool gauges across replicas. Use per-replica alerts, or an
explicit aggregate such as `min` when you want to alert on any replica reporting
an empty pool.

Specs: [proxy-runtime-observability](https://github.com/Soju06/codex-lb/tree/main/openspec/specs/proxy-runtime-observability)
and [replica-operations](https://github.com/Soju06/codex-lb/tree/main/openspec/specs/replica-operations).
