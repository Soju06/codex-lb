## MODIFIED Requirements

### Requirement: Codex compact requests are bounded by the proxy request budget
When `/backend-api/codex/responses/compact` is called for Codex auto-compaction, the service MUST bound the upstream compact call by the remaining proxy compact request budget. That budget (the dashboard `compact_request_budget_seconds`) is the only total cap on the upstream compact call; there is no separate upstream compact timeout setting. The default `compact_request_budget_seconds` MUST be 900 seconds so that, after the settlement reserve, a valid long compaction receives an upstream window of about 870 seconds rather than 150 seconds, and that default MUST NOT exceed the default `proxy_account_lease_ttl_seconds` (`account-lease-ttl-covers-compact-budget`). The core compact client MUST apply the configured `compact_request_budget_seconds` as its total timeout when no per-request remaining-budget override is present, and the smaller of the configured budget and the override when both are present. The compact SSE idle deadline MUST be governed by the per-request override when present and otherwise by `stream_idle_timeout_seconds`; it MUST NOT be replaced by the configured compact budget. The service MUST preserve Codex turn metadata `request_kind` in compact request logs so auto-compaction failures are distinguishable from normal user turns.

#### Scenario: auto-compaction cannot hang past the proxy budget
- **GIVEN** a Codex compact request carries `x-codex-turn-metadata` with `request_kind: "compaction"`
- **WHEN** the service calls upstream
- **THEN** the upstream call receives both connect and total timeout overrides from the remaining compact request budget
- **AND** no total timeout other than `compact_request_budget_seconds` (configured or remaining) is applied to the upstream compact call
- **AND** the request log records `request_kind` as `compaction`

#### Scenario: slow valid compaction receives the default 900-second budget
- **GIVEN** default configuration
- **WHEN** the compact service starts its upstream call
- **THEN** the remaining-budget override pushed to the core client is 870 seconds (900 minus the 30-second settlement reserve)
- **AND** the proxy does not impose the former 150-second upstream window

#### Scenario: explicit smaller compact budget caps the core client
- **GIVEN** an operator configures `compact_request_budget_seconds` below the default
- **WHEN** the core compact client starts an upstream call
- **THEN** the aiohttp total timeout does not exceed the configured compact budget
- **AND** a smaller per-request remaining-budget override shortens it further
- **AND** cancellation and timeout settlement remain unchanged

#### Scenario: compact idle and total deadlines remain independent
- **GIVEN** a compact request has an explicit remaining total-timeout override
- **WHEN** the core compact client starts an SSE upstream call
- **THEN** the overall request deadline uses the remaining total-timeout override
- **AND** the SSE idle deadline uses that explicit override, or `stream_idle_timeout_seconds` when no override is present
