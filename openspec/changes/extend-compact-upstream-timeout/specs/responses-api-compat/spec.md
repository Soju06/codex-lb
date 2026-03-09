## ADDED Requirements

### Requirement: Backend Codex compact requests honor dedicated upstream read timeout
When forwarding backend Codex compact requests upstream, the service MUST use a dedicated compact upstream read-timeout budget instead of a hard-coded 60 second limit. The effective timeout budget for waiting on upstream compact response bytes MUST follow `compact_upstream_read_timeout_seconds`.

#### Scenario: Compact request waits beyond legacy 60 second limit
- **WHEN** `/backend-api/codex/responses/compact` forwards a request upstream and `compact_upstream_read_timeout_seconds` is greater than 60
- **THEN** the upstream client uses that configured timeout budget for socket reads instead of aborting after 60 seconds

#### Scenario: Compact request still fails after configured idle timeout
- **WHEN** upstream compact response bytes do not arrive before the configured timeout budget expires
- **THEN** the service returns a 502 OpenAI-format error envelope instead of hanging indefinitely
