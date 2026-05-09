## ADDED Requirements

### Requirement: Websocket Responses full replay trims completed upstream output
When the proxy converts a client-side websocket full replay into a continuity-preserving upstream request, it MUST remove leading input items that represent already-completed upstream output for the injected `previous_response_id`. The proxy MUST keep the original input item count and prefix fingerprint for continuity metadata before request-payload trimming so later replay detection still compares against the client-visible full history.

#### Scenario: anchored replay drops previous assistant output before forwarding
- **WHEN** a websocket Responses request omits `previous_response_id`
- **AND** its input starts with assistant output or tool-call output items from the last completed upstream response
- **AND** the stored input prefix fingerprint matches that completed response
- **THEN** the proxy injects the completed `previous_response_id`
- **AND** forwards only the new trailing input items to upstream
- **AND** stores continuity metadata using the original untrimmed input count and fingerprint

#### Scenario: injected anchors wait for pending continuity metadata
- **WHEN** a websocket request can be anchored from a full replay
- **AND** another in-flight request still owns the continuity metadata needed for that anchor
- **THEN** the proxy waits within the remaining upstream request budget before forwarding the anchored request
- **AND** it fails with a retryable upstream timeout if the continuity metadata is still unavailable when the budget expires

#### Scenario: safe fresh replay is size bounded
- **WHEN** upstream reports `previous_response_not_found` for a proxy-injected replay anchor
- **AND** the original full replay payload can be resent within the upstream request size limit
- **THEN** the proxy MAY retry as a fresh full replay without the injected anchor
- **AND** it MUST NOT mark oversized original full replay payloads as retry-safe
