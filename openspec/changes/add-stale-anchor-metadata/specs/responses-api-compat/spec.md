## ADDED Requirements

### Requirement: WebSocket stale-anchor failures include diagnostic metadata
When a direct Responses WebSocket request fails closed because upstream rejects `previous_response_id` with `previous_response_not_found`, the service MUST emit stale-anchor diagnostic metadata in operator logs and request-log failure metadata. The metadata MUST distinguish `previous_response_source` (`client_supplied`, `proxy_injected`, or `unknown`), whether a fresh no-anchor replay body was available, owner lookup outcome/source, whether the matched previous response belongs to the same Codex session when known, and the previous-response age in seconds when known. The metadata MUST NOT expose raw `previous_response_id` values or request payload content.

#### Scenario: client-supplied stale anchor is classifiable
- **GIVEN** a direct WebSocket request arrives with a client-supplied `previous_response_id`
- **AND** upstream rejects that anchor with `previous_response_not_found`
- **THEN** the continuity failure log and request-log failure metadata identify `previous_response_source=client_supplied`
- **AND** they include owner lookup and replay-availability metadata without raw response ids

#### Scenario: proxy-injected stale anchor is classifiable
- **GIVEN** codex-lb injects a session-continuity `previous_response_id` into a direct WebSocket request
- **AND** upstream rejects that anchor with `previous_response_not_found`
- **THEN** the continuity failure log and request-log failure metadata identify `previous_response_source=proxy_injected`
- **AND** they state whether a retry-safe fresh no-anchor replay body was available
- **AND** owner lookup, age, and same-session fields remain explicit as `unknown` when unavailable rather than being omitted

#### Scenario: stale anchor owner hit records age and session relationship
- **GIVEN** owner lookup finds a previous response row for the rejected anchor
- **WHEN** the direct WebSocket request fails closed with `previous_response_not_found`
- **THEN** the stale-anchor diagnostics include the owner lookup source
- **AND** include previous-response age seconds and same-session status when those values can be derived

#### Scenario: account-only cache hits do not guess owner session metadata
- **GIVEN** owner resolution hits a request cache entry that retains the account id but not the matched request-log row
- **WHEN** the direct WebSocket request fails closed with `previous_response_not_found`
- **THEN** the stale-anchor diagnostics identify the owner lookup source as the request cache
- **AND** leave previous-response age and same-session status unknown rather than inferring them from the current request scope
