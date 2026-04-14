### MODIFIED Requirements

### Requirement: Websocket responses advertise and honor Codex turn-state affinity
When serving websocket Responses endpoints, the service MUST advertise an `x-codex-turn-state` header during websocket accept. If the client reconnects and presents that same `x-codex-turn-state`, the service MUST treat it as the highest-priority Codex-affinity key for upstream routing on that websocket turn. On `/v1/responses`, a proxy-generated turn-state MUST NOT override the first request's prompt-cache routing unless the client explicitly sends the turn-state back. When a downstream websocket session has no pending requests and receives no client traffic for the configured downstream idle timeout, the service MUST close that downstream websocket session and release any associated upstream session state.

#### Scenario: idle downstream websocket session is reclaimed
- **WHEN** a client opens a websocket Responses route
- **AND** the session has no pending requests
- **AND** no client traffic arrives before the configured downstream idle timeout elapses
- **THEN** the service closes the downstream websocket session
- **AND** it releases the associated upstream websocket session instead of holding proxy websocket capacity indefinitely
