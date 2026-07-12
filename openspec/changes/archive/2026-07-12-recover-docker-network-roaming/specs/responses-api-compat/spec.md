## ADDED Requirements

### Requirement: Pre-visible Responses requests recover from local network transitions

When a Responses request encounters a classified local DNS or host-route failure before any model output is exposed downstream, the proxy MUST retry on the same account with bounded backoff until the attempt succeeds or the existing request budget expires. Recovery MUST NOT move account-owned continuation or file state to another account. Existing keepalive behavior MUST remain active while an HTTP/SSE client waits.

#### Scenario: HTTP stream survives a temporary DNS outage

- **WHEN** a streaming Responses request fails DNS resolution before a response event is exposed
- **AND** DNS resolution recovers before the request budget expires
- **THEN** the proxy retries the request on the same account
- **AND** the downstream stream receives the recovered upstream response instead of a terminal network error

#### Scenario: Native WebSocket connect survives a temporary DNS outage

- **WHEN** a native Responses WebSocket request cannot open its upstream WebSocket because of a classified local network failure
- **AND** connectivity recovers before the request budget expires
- **THEN** the proxy opens the upstream WebSocket on the same account
- **AND** does not exhaust or exclude unrelated accounts

#### Scenario: Recovery remains bounded

- **WHEN** the local network does not recover before the configured request budget expires
- **THEN** the proxy terminates the request with its existing request-timeout contract
- **AND** does not extend the deadline or replay downstream-visible output
