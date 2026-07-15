## MODIFIED Requirements

### Requirement: Long Codex websocket turns tolerate extended upstream silence
The default compact request budget MUST be at least 180 seconds, and the default upstream stream idle timeout MUST be at least 600 seconds, so long-running Codex turns can survive expensive compaction or tool execution without a local proxy watchdog ending the turn prematurely. Responses streams over both HTTP and WebSocket transports MUST use `http_responses_stream_request_budget_seconds` when it is configured; they MUST fall back to `proxy_request_budget_seconds` only when no stream-specific budget is available.

#### Scenario: compact and stream watchdog defaults leave room for long turns
- **WHEN** the service starts with default configuration
- **THEN** `compact_request_budget_seconds` is at least 180 seconds
- **AND** `stream_idle_timeout_seconds` is at least 600 seconds

#### Scenario: WebSocket Responses stream uses the stream-specific request budget
- **GIVEN** `proxy_request_budget_seconds = 600`
- **AND** `http_responses_stream_request_budget_seconds = 7200`
- **WHEN** a native WebSocket Responses stream computes its request deadline
- **THEN** the stream budget is 7200 seconds
- **AND** the generic 600 second proxy request budget does not terminate the turn

#### Scenario: WebSocket reconnect keeps the stream-specific deadline
- **GIVEN** `proxy_request_budget_seconds = 600`
- **AND** `http_responses_stream_request_budget_seconds = 7200`
- **AND** a native WebSocket Responses request needs to reconnect after more than 600 seconds but less than 7200 seconds
- **WHEN** the reconnect performs account selection and opens its replacement upstream WebSocket
- **THEN** both operations remain bounded by the original 7200-second stream deadline
- **AND** the reconnect does not fail solely because the generic 600-second budget elapsed
