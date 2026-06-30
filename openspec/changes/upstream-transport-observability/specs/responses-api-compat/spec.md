## ADDED Requirements

### Requirement: Request logs expose upstream Responses transport
For streaming Responses proxy requests, persisted request logs MUST distinguish the downstream client transport from the upstream egress transport by recording the upstream transport in `request_logs.upstream_transport` while preserving `request_logs.transport` as the downstream client transport.

#### Scenario: downstream HTTP single-shot records upstream HTTP
- **GIVEN** the downstream request transport is HTTP
- **AND** smart HTTP-downstream routing chooses upstream HTTP for a single-shot Responses request
- **WHEN** the request log is persisted
- **THEN** `transport` is `"http"`
- **AND** `upstream_transport` is `"http"`

#### Scenario: downstream HTTP sticky records preserved auto upstream mode
- **GIVEN** the downstream request transport is HTTP
- **AND** smart HTTP-downstream routing keeps the base upstream `"auto"` mode for a sticky Responses request
- **WHEN** the request log is persisted
- **THEN** `transport` is `"http"`
- **AND** `upstream_transport` is `"auto"`

#### Scenario: historical or unrelated rows tolerate missing upstream transport
- **GIVEN** a request log row predates upstream transport persistence or belongs to a request kind that does not know its upstream transport
- **WHEN** the row is read
- **THEN** `upstream_transport` MAY be null
- **AND** the existing request-log response MUST remain valid

### Requirement: Request Logs API returns upstream transport
The Request Logs API MUST include `upstream_transport` on each request log entry so operators and dashboards can query upstream egress transport without overloading the existing downstream `transport` field.

#### Scenario: request logs response includes upstream transport
- **GIVEN** a persisted request log has `transport = "http"` and `upstream_transport = "auto"`
- **WHEN** a dashboard client fetches request logs
- **THEN** the returned entry includes `transport: "http"`
- **AND** the returned entry includes `upstream_transport: "auto"`

### Requirement: Upstream transport decisions emit low-cardinality metrics
Streaming Responses proxy requests MUST emit a low-cardinality Prometheus counter for upstream transport decisions. The metric MUST NOT include request id, account id, API key id, model, prompt cache key, or other high-cardinality identifiers.

#### Scenario: transport decision counter labels are bounded
- **WHEN** a streaming Responses request completes or terminates with an error
- **THEN** `codex_lb_upstream_transport_decisions_total` is incremented once
- **AND** its labels include only `downstream_transport`, `upstream_transport`, `policy`, `sticky`, and `status`
- **AND** `status` is `"success"` or `"error"`
