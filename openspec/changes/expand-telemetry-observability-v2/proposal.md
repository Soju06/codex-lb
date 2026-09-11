## Why

The anonymous telemetry payload (`schema_version: 1`) collects too little to answer the
questions the project actually asks of it, and two of its current contracts make the numbers
it does produce hard to interpret.

Measured on the production collector 2026-09-09 (D1 `codex-lb-telemetry`, 15,677 snapshots):

- **Performance is one number wide.** The payload carries `latency_ms_p50`, `ttft_ms_p50`, and
  `ttft_ms_p95` as instance-global scalars. Tokens-per-second is absent entirely even though
  `reports` already computes it, so fleet TPS could only be reconstructed by dividing
  instance-level aggregates, which mixes cohorts and cannot be aggregated across the fleet.
  Percentiles of a percentile are not a fleet percentile: with only per-instance p50/p95 there
  is no correct way to compute a fleet p95.
- **Dimension statistics are ratios without denominators-per-dimension.** `clients`, `models[]`,
  `transport_mix`, and `service_tier_mix` are shares of one instance-global `requests` count, so
  a fleet total per model or client can only be estimated by re-multiplying shares.
- **Error visibility is five names.** `top_upstream_errors` is the top 5 `upstream_error_code`
  values by count with no counts attached, no HTTP status distribution, no cancellation rate,
  and no internal failure phase, so an upstream incident cannot be sized from telemetry.
- **Account scale is unanswerable, not merely coarse.** `accounts.pool_bucket` and
  `plan_mix` are count buckets whose top bin `100+` is open-ended, so the fleet account total has
  a lower bound and no upper bound. Answering "how many accounts does the fleet run" currently
  requires inventing a cap.
- **The rolling window cannot be summed.** `usage_7d` is `[now-7d, now)` recomputed at every
  transmission, and the scheduler transmits immediately at process start. Consecutive snapshots
  therefore overlap by ~6 days, and a restart-heavy instance contributes its window many times.
  Any fleet total built by adding `usage_7d.requests` across snapshots double counts by an
  unknown factor.
- **Server-side retention is unspecified.** `context.md` tells operators to assume snapshots are
  stored indefinitely, which is a weaker privacy statement than the project intends and is not
  disclosed as a concrete number in the consent dialog.

The privacy allowlist model itself is working and is kept: no raw user-agent strings, no model
names outside the catalog, no free-text error messages, no per-account records.

## What Changes

- Introduce snapshot `schema_version: 2` with two distinct outbound bodies: a small **heartbeat**
  carrying current instance state, and **completed-UTC-day aggregates** that are idempotent and
  summable. Rolling `usage_7d` is retained on the heartbeat as an explicitly non-summable state
  value.
- Day aggregates are keyed by `(instance_id, utc_date, schema_version)` and upserted, so restarts,
  retries, and duplicate deliveries cannot double count. An instance backfills at most the last
  7 completed days it has not yet acknowledged, one body per day, bounded per tick.
- Replace instance-global scalar percentiles with **fixed log-scale histogram bucket counts** per
  metric (`latency_ms`, `ttft_ms`, `tps`). Histograms are exactly mergeable, so the collector
  computes fleet p50/p95/p99 from summed buckets instead of averaging per-instance percentiles.
- Attach those histograms to four independent dimensions — model, client family, transport, and
  service tier — plus a global roll-up. **Cross-dimension (model × client and similar) aggregation
  is forbidden** so no sparse combination can single out an installation.
- Carry **exact counts per allowlisted dimension** (model, client family, transport,
  upstream transport, service tier, request kind, error class) with an explicit `sample_count`
  on every histogram, making fleet totals exact rather than share-derived.
- Add an **error taxonomy**: stable upstream error class, internal failure phase, and HTTP status
  class, each with exact counts, plus success/error/cancelled totals. Unregistered values map to
  `other`. Raw `error_message`, `failure_detail`, and exception type strings remain forbidden.
- Send **exact account counts** — pool total, per-plan, and per-status — from every active
  installation including `undecided` consent. API key count, cost, and database size stay
  bucketed. This resolves the open-ended `100+` bin for accounts only.
- Bump the consent notice version so existing installations are shown the new exact payload
  preview one more time. The informed opt-out model, the default-on behavior, and the
  environment kill switch are unchanged.
- Specify collector retention as **365 days for per-instance detail** (D1 hot for the most recent
  90 days, R2 cold for the remainder) with non-identifying fleet-day aggregates retained beyond
  that, and disclose that duration in the consent surface.
- Specify exposure tiers: per-instance detail is admin-authenticated only; public surfaces serve
  only fleet aggregates whose contributing-instance count meets a minimum threshold.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `telemetry`: schema v2 payload split (heartbeat + idempotent completed-day aggregates), fixed
  histogram wire format with per-dimension exact counts and a cross-dimension prohibition, error
  taxonomy, exact account counts with the remaining sensitive aggregates still bucketed, consent
  notice re-display, disclosed retention duration, and collector exposure tiers.

## Impact

- Affected code: `app/modules/telemetry/schemas.py` (v2 bodies), `snapshot.py` (histogram
  accumulation, per-dimension exact counts, error taxonomy, exact account counts, completed-day
  windows), `sender.py` (heartbeat and day-aggregate transmission, backfill loop, acknowledgement
  state), `scheduler.py` (tick splits heartbeat from day aggregates), `consent.py` and
  `api.py` (notice version), `app/db/models.py` plus one Alembic revision (per-day transmission
  acknowledgement watermark, consent notice version), frontend telemetry consent dialog and
  settings preview.
- Affected tests: wire-schema allowlist tests extended to both v2 bodies, histogram merge and
  percentile-reconstruction tests, day-aggregate idempotency and backfill-bound tests, error
  taxonomy allowlist tests, exact-account-count privacy tests asserting keys/cost/DB stay
  bucketed, cross-dimension prohibition test, consent notice re-display test.
- Collector repository (`Soju06/codex-lb-telemetry`, separate PR): v2 ingest endpoints, per-day
  upsert key, histogram storage and fleet percentile queries, D1-hot/R2-cold tiering with the
  365-day boundary, admin versus public exposure split with the aggregate threshold.
- Payload size grows from a measured mean of 1,491 bytes (max 3,213) to an estimated ~10 KB per
  completed-day body; the heartbeat stays in the current size class. Transmission stays once per
  24 h plus bounded backfill.
- Non-goals (documented): unique-account deduplication across installations, cross-dimension
  aggregation, raw per-request sample transmission, exact API key counts, exact cost, exact
  database size, and any change to the default-on consent model.
