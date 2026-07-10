## ADDED Requirements

### Requirement: TTFT phase timings are persisted and exported
The proxy MUST persist nullable low-cardinality request-log fields for TTFT phase analysis and MUST export equivalent Prometheus phase latency observations without labels containing raw API keys, raw session ids, raw affinity keys, request ids, or prompt text.

#### Scenario: HTTP bridge request records phase timing
- **WHEN** a visible HTTP bridge request waits for session response-create admission and then receives upstream `response.created`
- **THEN** the request log includes integer millisecond timing for response-create gate wait and upstream response-created latency
- **AND** Prometheus observes phase latency with only stable labels such as phase, transport, upstream transport, and model class

#### Scenario: First upstream event is distinct from first token
- **WHEN** the upstream bridge reader receives an upstream event before text delta output
- **THEN** the request log can record first upstream event latency separately from first downstream token latency

### Requirement: Codex prewarm canary outcomes are observable
The proxy MUST record visible-request prewarm status, latency, canary bucket, and eligibility cohort using stable strings, and MUST emit a prewarm outcome counter labelled only by outcome, cohort, and bucket.

#### Scenario: Canary miss is visible without raw identifiers
- **WHEN** Codex prewarm is enabled but deterministic canary sampling excludes an otherwise eligible request
- **THEN** the visible request log records `prewarm_status=canary_miss`
- **AND** metrics increment the prewarm counter for the stable cohort and bucket
- **AND** logs and metrics do not include raw API keys, raw session ids, prompt text, or affinity key values

### Requirement: 24-hour TTFT breakdown queries are available
Operators MUST have an OpenSpec context runbook or dashboard artifact with 24-hour TTFT breakdown queries by user agent group, upstream transport, model/cache ratio, session gap cohort, prompt size cohort, and prewarm bucket/outcome/cohort.

#### Scenario: Operator investigates TTFT regression
- **WHEN** an operator needs to inspect the last 24 hours of request-log latency
- **THEN** the repository provides SQL that reports p50, p90, p95 TTFT and total latency for the requested breakdowns
