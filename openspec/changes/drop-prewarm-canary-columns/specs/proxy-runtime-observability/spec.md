## MODIFIED Requirements

### Requirement: Codex prewarm canary outcomes are observable

The proxy MUST record visible-request prewarm status and latency using
stable strings, and MUST emit a prewarm outcome counter labelled only by
outcome. Prewarm eligibility is the prewarm enabled flag alone: no
deterministic canary sampling or allow/deny cohort exists, so no canary
bucket or eligibility cohort dimension is recorded and the
`prewarm_status=canary_miss` value MUST NOT occur. The request log schema
MUST NOT carry canary bucket or eligibility cohort columns.

#### Scenario: Prewarm outcome is visible without raw identifiers

- **WHEN** Codex prewarm is enabled and a visible request triggers or skips
  a session prewarm
- **THEN** the visible request log records `prewarm_status` (and prewarm
  latency when a prewarm was attempted)
- **AND** metrics increment the outcome-labelled prewarm counter
- **AND** logs and metrics do not include raw API keys, raw session ids,
  prompt text, or affinity key values

#### Scenario: Canary sampling no longer excludes eligible requests

- **WHEN** Codex prewarm is enabled
- **THEN** no request is excluded by deterministic canary sampling
- **AND** `prewarm_status=canary_miss` is never recorded
- **AND** the prewarm counter and request log carry no canary bucket or
  eligibility cohort dimension

#### Scenario: Legacy canary columns are dropped by migration

- **GIVEN** a database migrated through `20260709_000000_add_ttft_phase_observability`
  whose `request_logs` rows still hold historical `prewarm_canary_bucket` /
  `prewarm_eligible_reason` values
- **WHEN** the schema upgrades to `20260908_000000_drop_prewarm_canary_columns`
- **THEN** both columns are removed on SQLite and PostgreSQL
- **AND** the remaining request-log fields of those rows (including
  `prewarm_status` and `prewarm_latency_ms`) are preserved
- **AND** re-running the upgrade is a no-op, and downgrading re-adds both
  columns as nullable
