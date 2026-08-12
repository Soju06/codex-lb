# proxy-runtime-observability

## ADDED Requirements

### Requirement: Stream pool congestion is observable

When Prometheus support is available the service MUST expose a gauge named `codex_lb_stream_pool_capacity` whose value equals the fair-share gate's most recently computed candidate pool capacity and a gauge named `codex_lb_stream_pool_inflight` whose value equals the corresponding pool in-flight stream count, and a counter named `codex_lb_api_key_fair_share_rejections_total` incremented once per fair-share denial. The gauges and the counter MUST NOT carry API-key, account, or request labels. Each fair-share denial MUST log at warning level with the requesting `api_key_id`, the key's in-flight count, the computed fair share, the pool in-flight and capacity, and the active-key count, and MUST NOT include other keys' identifiers, instance secrets, or request payload content. All fair-share metrics MUST degrade to no-ops when the Prometheus client is absent.

#### Scenario: Pool gauges are exported during gate evaluation

- **GIVEN** the fair-share gate is enabled and evaluates a stream selection
- **WHEN** metrics are scraped
- **THEN** `codex_lb_stream_pool_capacity` and `codex_lb_stream_pool_inflight` report the evaluated pool values without per-key or per-account labels

#### Scenario: Denials are counted without key cardinality

- **GIVEN** repeated fair-share denials for multiple keys
- **WHEN** metrics are scraped
- **THEN** `codex_lb_api_key_fair_share_rejections_total` reflects the total denial count with no per-key label

#### Scenario: Denial log carries the diagnostic numbers

- **GIVEN** a fair-share denial
- **WHEN** the warning is logged
- **THEN** it includes the requester's `api_key_id`, key in-flight count, fair share, pool in-flight, pool capacity, and active-key count and no other key's identifier
