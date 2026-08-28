## MODIFIED Requirements

### Requirement: Codex prewarm canary outcomes are observable

The proxy MUST record visible-request prewarm status and latency using
stable strings, and MUST emit a prewarm outcome counter labelled only by
outcome. Prewarm eligibility is the prewarm enabled flag alone: no
deterministic canary sampling or allow/deny cohort exists, so no canary
bucket or eligibility cohort dimension is recorded and the
`prewarm_status=canary_miss` value MUST NOT occur.

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
  eligibility cohort dimension (the legacy request-log columns remain
  unwritten for one release for rolling-upgrade safety, then are dropped)

### Requirement: 24-hour TTFT breakdown queries are available

Operators MUST have an OpenSpec context runbook or dashboard artifact with
24-hour TTFT breakdown queries by user agent group, upstream transport,
model/cache ratio, session gap cohort, prompt size cohort, and prewarm
status/outcome.

#### Scenario: Operator investigates TTFT regression

- **WHEN** an operator needs to inspect the last 24 hours of request-log
  latency
- **THEN** the repository provides SQL that reports p50, p90, p95 TTFT and
  total latency for the requested breakdowns
