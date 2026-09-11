# Design — telemetry schema v2

## Owner decisions (2026-09-09, binding)

Recorded during the design interview. Rank later work from these, not from the options that
were considered alongside them.

| # | Decision | Chosen |
|---|---|---|
| D1 | Who sends exact account counts | All active installations including `undecided` consent. Unique-account deduplication across installations is **not** attempted. |
| D2 | Which sensitive aggregates go exact | Account total, per-plan counts, per-status counts. API key count, cost, and database size **stay bucketed**. |
| D3 | Time contract | Immediate heartbeat plus completed-UTC-day aggregates, idempotent upsert, at most 7 days backfill. Rolling 7-day values remain a non-summable state value. |
| D4 | Dimension statistics shape | Exact counts per allowlisted dimension with `sample_count`; **cross-dimension aggregation forbidden**. |
| D5 | Performance dimensions | Histograms on model, client family, transport, and service tier, plus global. |
| D6 | Distribution wire format | Fixed log-scale histogram bucket counts; the collector computes percentiles. |
| D7 | Error detail | Stable error class + failure phase + HTTP status class, exact counts, unregistered values fold to `other`, raw strings forbidden. |
| D8 | Consent for v2 | Default-on retained; existing installations are shown the new exact payload preview once more. |
| D9 | Retention | Per-instance detail 365 days; non-identifying fleet-day aggregates retained beyond that. |
| D10 | Storage tiering | D1 hot for the most recent 90 days, R2 cold for the remainder of the 365-day window. |
| D11 | Exposure | Admin-authenticated access to per-instance detail; public surfaces serve only threshold-gated fleet aggregates. |

## Why the current numbers are wrong, precisely

Two defects are worth stating exactly because they are what the v2 shape is designed around.

**Percentiles do not average.** With only `ttft_ms_p50` and `ttft_ms_p95` per instance, a fleet
p95 cannot be recovered. Taking the median of per-instance p50s answers "what is the typical
instance's typical request", not "what is the typical request", and the two diverge whenever
instances differ in traffic volume — which they do by four orders of magnitude in the measured
fleet (`requests` p10 = 111, p90 = 56,671). Histograms fix this because bucket counts add.

**Overlapping windows do not sum.** `usage_7d` covers `[now-7d, now)` at transmission time and
the scheduler transmits at process start. Two snapshots a day apart share six days of traffic.
Adding `requests` across snapshots therefore multiplies real traffic by an unknown factor that
depends on each instance's restart frequency. Completed UTC days keyed by date are disjoint, so
they sum, and an idempotent upsert makes redelivery harmless.

## Two bodies, one identity

`POST /v1/snapshot` keeps carrying **current state** and becomes the heartbeat:

- instance descriptors (`version`, `python`, `os`, `arch`, `uptime_hours`, `consent`)
- `deploy`, `features`, `accounts` (now exact per D1/D2)
- `usage_7d`, explicitly marked non-summable and retained only so the existing operator-facing
  preview and dashboards keep working

`POST /v1/day` carries one **completed UTC day** of traffic. Body key is
`(instance_id, utc_date, schema_version)`; the collector upserts on that key. Both bodies use the
existing Ed25519 instance identity and signing scheme — v2 introduces no new identity concept, so
the "random instance identity" requirement is untouched.

A local watermark records the last acknowledged `utc_date`. On each tick the instance sends the
heartbeat, then sends unacknowledged completed days newest-first, **at most 7 bodies per tick**.
Days older than 7 completed days are marked acknowledged without transmission so a long outage
cannot produce an unbounded backlog.

Why one body per day rather than an array of days: it keeps each request in the ~10 KB class,
makes partial backfill failure isolate to a single day, and makes the collector's idempotency key
a property of the request rather than of an element inside it.

## Histogram wire format

Fixed, hard-coded bucket edges — not configurable, not adaptive — so that any two payloads from
any two versions are mergeable without negotiation.

```
latency_ms, ttft_ms edges (ms):
  0, 50, 100, 200, 400, 800, 1600, 3200, 6400, 12800, 25600, 51200, 102400, +inf   (14 buckets)

tps edges (tokens/s):
  0, 5, 10, 20, 40, 80, 160, 320, 640, 1280, +inf                                   (11 buckets)
```

Encoding is sparse: a map of bucket index to count, omitting zeros, plus `sample_count`.

```json
{"sample_count": 1240, "buckets": {"4": 61, "5": 402, "6": 588, "7": 173, "8": 16}}
```

`sample_count` MUST equal the sum of bucket counts. It is carried explicitly rather than derived
so a collector can detect a truncated or degraded body.

Reconstruction error is bounded by bucket width: a p95 landing in the `3200–6400` bucket is
reported as an interval, and the collector reports the bucket boundary rather than interpolating
a false-precision value. This is a deliberate trade against DDSketch, which would give tighter
relative error at the cost of a non-obvious merge implementation on both ends; fixed buckets are
inspectable in the consent preview, which matters more here than tail precision.

`tps` is computed with the definition `reports` already uses:
`(output_tokens - coalesce(reasoning_tokens, 0)) * 1000 / (latency_ms - latency_first_token_ms)`,
sampled only from rows where the denominator is positive and the numerator is above zero.

## Dimensions and the cross-dimension prohibition

Five independent dimension lists, each an array of `{name, requests, ...histograms}`:

| Dimension | Allowlist | Cap |
|---|---|---|
| `models` | official model catalog, else `other` | top 10 by requests + `other` |
| `clients` | canonical family mapping table in `spec.md` | the 10 fixed families |
| `transport` | `ws`, `http_bridge` | 2 |
| `upstream_transport` | `ws`, `http` | 2 |
| `service_tier` | `default`, `flex`, `priority` | 3 |

Each dimension is a **marginal** of the same day. The payload never contains a cell keyed by two
dimensions at once. This is the load-bearing privacy property of the expansion: marginals of a
day's traffic are hard to trace to an installation, while a sparse
`model × client × service_tier` cell can be near-unique for an unusual operator. The wire-schema
test asserts the absence of any nested dimension key, so the prohibition is mechanically enforced
rather than merely documented.

D5 attaches histograms to all four analysis dimensions plus global. Worst case entry count is
11 + 10 + 2 + 2 + 3 = 28, each carrying three sparse histograms.

## Error taxonomy

Three independent count maps, each an allowlist with `other` as the catch-all:

- `upstream_error_class` — stable upstream error codes (`usage_limit_reached`,
  `server_overloaded`, and the rest of the enum), exact counts, no top-5 truncation
- `failure_phase` — internal phase enum (connect, response-create gate, bridge queue, stream,
  settle) so an incident can be localized without free text
- `http_status_class` — `2xx`, `4xx`, `429`, `5xx` counted separately, with `429` split out
  because rate limiting is the signal most often asked about

Plus `outcomes: {success, error, cancelled}` as exact counts. `error_message`,
`failure_detail`, `failure_exception_type`, and `bridge_stage` free-text values stay forbidden by
the existing allowlist requirement.

## Exact accounts, still-bucketed everything else

D2's split is drawn on what the value reveals. An account total and its plan/status breakdown
describe **capacity shape**, which is what fleet capacity planning needs and which many operators
would answer publicly. API key count, seven-day cost, and database size correlate with
organization size and spend in a way that account count alone does not, so they keep their
buckets and the requirement that names them stays in force for those three.

The open-ended `100+` bin is what made a fleet account total unanswerable. Exact counts remove
the estimation entirely for accounts. They do **not** produce a unique-account count: the same
upstream ChatGPT account configured in two installations is counted twice, and D1 explicitly
declines to attempt deduplication. Every surface that reports this number must label it as local
account rows, never as unique accounts or users.

## Consent

The consent model does not change: default-on while `undecided`, one-time dialog, settings
toggle, environment kill switch, and the existing precedence order. What changes is that the
payload being consented to is materially larger and now contains exact account counts, so the
notice version is bumped and installations that already decided are shown the new exact preview
once. A decision already on record is preserved — the re-display informs, it does not reset
consent to `undecided`.

The disclosed retention duration (D9) becomes part of that notice, replacing the current
"assume stored indefinitely" language in `context.md`.

## Retention and storage tiering

Per-instance detail is retained for 365 days. Non-identifying fleet-day aggregates — the summed
histograms and dimension counts with no `instance_id` — are retained beyond that so long-run
trend series survive detail expiry.

Tiering (D10) puts the most recent 90 days in D1 and the remainder in R2. Sizing from measured
values: ~1,300 instances with usage times ~10 KB per completed-day body is ~13 MB/day, so 90 days
of hot detail is ~1.2 GB against D1's 10 GB per-database limit, and the full 365-day cold tail is
~4.7 GB in R2 (~$0.07/month at $0.015/GB-month). A single-D1 year would sit at ~4.7 GB — under
the limit today, but with no headroom for fleet growth and no margin against the account-level
1 TB cap, which is why the cold tier is specified now rather than after the limit is hit.

## Exposure tiers

Per-instance rows are admin-authenticated only. Public surfaces — badges, any public stats
endpoint — serve only fleet aggregates, and only cells whose contributing-instance count meets a
minimum threshold, so a dimension with one contributor is never published.

This interacts with an already-known reporting hazard: `active_7d` counts an installation for a
full week after it stops reporting, and one-shot registrations dominate the population
(7,191 of 8,259 registered instances had exactly one accepted snapshot on 2026-09-09). v2 does not
fix that population question, and no surface may present any of these counts as unique users.

## Anti-goals

- **Unique-account deduplication.** Would require a stable cross-installation account identifier;
  explicitly rejected in D1.
- **Cross-dimension cells.** Rejected in D4 as the primary re-identification vector.
- **Raw per-request samples.** Rejected in D6; only aggregated bucket counts leave an instance.
- **Exact API keys / cost / database size.** Rejected in D2.
- **Diagnosing installation-identity churn.** `uptime_hours` is process uptime and `usage_7d`
  reads durable local history, so a normal restart of an old installation produces
  "usage present, uptime zero". That combination is not evidence of identity regeneration, and v2
  does not add any field intended to measure it.

## Open questions

None.
