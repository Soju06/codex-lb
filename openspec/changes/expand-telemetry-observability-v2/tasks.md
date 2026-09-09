# Tasks — expand-telemetry-observability-v2

## A. Payload contract

- [ ] A1. Define v2 Pydantic bodies in `app/modules/telemetry/schemas.py`: heartbeat envelope
  (existing snapshot shape at `schema_version: 2`) and the completed-day aggregate body keyed by
  `instance_id` + `utc_date` + `schema_version`.
- [ ] A2. Define the fixed histogram type (`sample_count` + sparse `buckets` map) and the
  hard-coded bucket edge tables for `latency_ms`, `ttft_ms`, and `tps`.
- [ ] A3. Define the dimension entry types (model, client family, transport, upstream transport,
  service tier, request kind) carrying exact `requests` plus histograms, with no nested
  cross-dimension key anywhere in the model tree.
- [ ] A4. Define the error taxonomy maps (upstream error class, failure phase, HTTP status class)
  and the `outcomes` counts.
- [ ] A5. Change `accounts` to exact total, per-plan, and per-status integers; leave
  `api_keys_bucket`, `cost_usd_bucket`, and `db_size_bucket` as buckets.
- [ ] A6. Document every new field in `openspec/specs/telemetry/context.md`, including the bucket
  edge tables, the retention duration, and the non-summable marking on `usage_7d`.

## B. Snapshot construction

- [ ] B1. Add completed-UTC-day windowing to `app/modules/telemetry/snapshot.py`, separate from
  the existing rolling `[now-7d, now)` query.
- [ ] B2. Accumulate histograms from `request_logs` for `latency_ms`, `latency_first_token_ms`,
  and the `reports` TPS expression, filtering rows where the TPS denominator is non-positive.
- [ ] B3. Emit exact per-dimension counts and per-dimension histograms for the five dimensions,
  applying the existing model-catalog and client-family allowlists with `other` folding, and the
  documented top-N cap on models.
- [ ] B4. Emit the error taxonomy counts from `upstream_error_code`, `failure_phase`, and
  `upstream_status_code`, mapping unregistered values to `other`.
- [ ] B5. Emit exact account total, per-plan, and per-status counts from the accounts table.

## C. Transmission and idempotency

- [ ] C1. Add a per-day acknowledgement watermark to the telemetry settings row; one Alembic
  revision on the current single head, with upgrade and downgrade.
- [ ] C2. Split the scheduler tick into heartbeat transmission followed by unacknowledged
  completed-day transmission, newest first, capped at seven bodies per tick.
- [ ] C3. Mark completed days older than the seven most recent as acknowledged without
  transmitting them.
- [ ] C4. Keep the existing bounded timeout, single retry, and debug-only failure logging on both
  body types; a day-body failure must not block the heartbeat or the remaining days.

## D. Consent

- [ ] D1. Add a consent notice version to the settings row and the consent API response.
- [ ] D2. Show the current exact payload preview once to installations whose acknowledged notice
  version is lower, preserving any persisted decision.
- [ ] D3. Render the collector retention duration in the consent dialog and the settings
  telemetry view.
- [ ] D4. Update the frontend telemetry dialog and settings preview for the v2 payload shape.

## E. Tests

- [ ] E1. Extend the outbound wire-schema allowlist test to both v2 bodies; it must fail on any
  undeclared field.
- [ ] E2. Add a cross-dimension prohibition test asserting no statistic is keyed by two dimensions.
- [ ] E3. Add histogram tests: `sample_count` equals bucket sum, merge across instances, and
  percentile reconstruction from summed buckets.
- [ ] E4. Add day-aggregate idempotency tests (same day twice yields one record) and backfill
  bound tests (thirty-day outage transmits at most seven bodies).
- [ ] E5. Add privacy tests asserting exact account counts are present while API key count, cost,
  and database size remain buckets, and that no free-text failure data is serialized.
- [ ] E6. Add a consent notice re-display test: higher notice version re-informs a decided
  operator without resetting the persisted decision.
- [ ] E7. Run the unfiltered unit suite plus the telemetry integration tests on both database
  backends.

## F. Validation

- [ ] F1. `openspec validate expand-telemetry-observability-v2 --strict` reports valid.
- [ ] F2. `make lint` and `uv run ty check` clean on changed Python files; revert `uv.lock` if
  touched.
- [ ] F3. Temp-DB `codex-lb-db upgrade head && codex-lb-db check` reports `schema_drift=none`.
- [ ] F4. Capture the rendered consent dialog and settings preview as before/after screenshots for
  the PR body.

## G. Collector (separate repository, separate PR)

- [ ] G1. `Soju06/codex-lb-telemetry`: v2 ingest endpoints with the per-day upsert key.
- [ ] G2. Histogram storage plus fleet percentile queries computed from summed buckets.
- [ ] G3. D1-hot / R2-cold tiering at the 90-day boundary with 365-day per-instance expiry and
  longer-lived non-identifying fleet aggregates.
- [ ] G4. Admin versus public exposure split with the documented minimum contributing-instance
  threshold on public aggregate cells.
