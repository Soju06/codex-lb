## MODIFIED Requirements

### Requirement: Codex prewarm canary outcomes are observable

The proxy MUST record visible-request prewarm status and latency using
stable strings, and MUST emit a prewarm outcome counter labelled only by
outcome. Prewarm eligibility is the prewarm enabled flag alone: no
deterministic canary sampling or allow/deny cohort exists, so no canary
bucket or eligibility cohort dimension is recorded and the
`prewarm_status=canary_miss` value MUST NOT occur. The request-log ORM model
MUST NOT map the legacy canary bucket or eligibility cohort columns. The
physical columns MAY remain in the schema, allow-listed by the schema-drift
gate, until the release after the last release whose ORM mapped them; they
MUST NOT be dropped while a supported previous-release replica still maps
them, because that replica renders explicit NULLs for them in every
request-log INSERT while the migration Job runs ahead of the workload roll.

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

#### Scenario: Legacy canary columns stay insertable during the rolling upgrade

- **GIVEN** a database at the current Alembic head
- **WHEN** a replica running the previous release inserts a request log with
  explicit NULL `prewarm_canary_bucket` / `prewarm_eligible_reason` values
- **THEN** the insert succeeds because both physical columns still exist
- **AND** the current release's `RequestLog` model does not map either column
- **AND** the schema-drift check reports no drift for the retained columns
