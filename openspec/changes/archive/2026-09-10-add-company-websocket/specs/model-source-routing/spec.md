## ADDED Requirements

### Requirement: Company WebSocket bridge
Both Responses WebSocket endpoints SHALL serve company source response.create frames through the existing HTTP source policy and forwarding path. They SHALL relay complete SSE JSON events as WebSocket messages, enforce authentication, budgets and source concurrency, and SHALL NOT fall back to subscription accounts. Disconnect or cancellation SHALL terminate the owned HTTP invocation and release its resources.

#### Scenario: Desktop company request
- **WHEN** a client requests an enabled company model over WebSocket
- **THEN** source response events reach the client without requiring HTTP fallback and the request is accounted once against the company source

### Requirement: Bounded company WebSocket continuation
The bridge SHALL retain only the latest successful response context per connection, bounded by the configured request frame byte limit. A matching previous_response_id on the same model SHALL expand input plus output plus the new input. Unknown or cross-model anchors SHALL fail explicitly. No failed or cancelled response SHALL establish a continuation anchor. Pipelined frames SHALL be bounded and unsupported control frames SHALL receive explicit errors.

#### Scenario: Tool result continuation
- **WHEN** a subsequent create references the latest successful response and includes its tool result
- **THEN** the upstream receives the original input, tool call and matching tool result in order
