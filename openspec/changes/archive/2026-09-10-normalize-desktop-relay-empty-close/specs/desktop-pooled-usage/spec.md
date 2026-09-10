## ADDED Requirements

### Requirement: Relay normalizes omitted WebSocket close status
The Desktop relay MUST forward a WebSocket close frame without a status code as normal closure code 1000. It MUST preserve explicit close codes and reasons.

#### Scenario: Upstream closes without a status code
- **WHEN** an upstream WebSocket sends an empty close frame through the relay
- **THEN** the Desktop client receives close code 1000 without a protocol error
