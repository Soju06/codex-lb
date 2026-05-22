## MODIFIED Requirements

### Requirement: Performance triage separates local health from upstream latency

Operator-facing health or performance guidance MUST distinguish local proxy health from request-tail latency and upstream behavior. The guidance MUST combine process/container health, readiness checks, recent local error-log patterns, request latency percentiles, recent upstream failure classes, requested service tier, and actual upstream service tier before recommending a local code change, restart, worker-count change, or upstream/tier investigation. The live snapshot request-log report MUST expose read-only perf-tail groups that include model, coarse route pattern, output-size bucket, reasoning effort, status, websocket failure class when present, average latency, p95 latency, and max latency. The report MUST expose long-generation subgroups, a separate websocket-instability summary with account identifiers hashed, and request-log query timing metadata separately from proxied request latency. Malformed live database JSON output MUST be reported as an explicit snapshot error.

#### Scenario: Healthy proxy has a slow request tail

- **WHEN** readiness checks pass
- **AND** the container has no recent restart, OOM, traceback, ASGI exception, or database-lock signal
- **AND** request logs still show elevated p90 or p95 latency
- **THEN** the guidance identifies the proxy as locally healthy
- **AND** it reports the latency tail separately from local health
- **AND** it does not recommend a local patch, restart, or worker-count change solely from the elevated latency percentile

#### Scenario: Requested tier differs from actual upstream tier

- **WHEN** request logs show `requested_service_tier` as `ultrafast`
- **AND** upstream responses report `actual_service_tier` as `default` or `auto`
- **THEN** the guidance treats the mismatch as an upstream/account/catalog verification issue first
- **AND** it does not require codex-lb to rewrite or downgrade the operator's requested tier

#### Scenario: Isolated upstream websocket failures occur amid successful traffic

- **WHEN** recent request logs contain isolated `stream_incomplete` or `upstream_websocket_open_timeout` rows
- **AND** later websocket requests for the same surface complete successfully
- **AND** local readiness and error-log checks remain clean
- **THEN** the guidance reports the failures as a caveat to monitor
- **AND** it does not classify the local proxy as unhealthy unless the failures become sustained or correlate with local runtime errors

#### Scenario: Snapshot separates report cost from proxied request latency

- **WHEN** an operator captures the live snapshot request-log report
- **THEN** the report includes request-log query timing metadata
- **AND** slow snapshot database reads are visible separately from proxied request latency

#### Scenario: Snapshot groups slow tails by request shape

- **WHEN** an operator captures the live snapshot request-log report
- **THEN** the report includes perf-tail groups with model, route pattern, output-size bucket, reasoning effort, status, websocket failure class when present, average latency, p95 latency, and max latency
- **AND** long-generation groups are exposed separately from short-output traffic
- **AND** websocket instability groups include hashed account identifiers rather than raw account ids
