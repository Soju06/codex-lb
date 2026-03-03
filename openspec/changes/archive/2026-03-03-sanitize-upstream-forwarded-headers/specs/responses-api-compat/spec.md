## ADDED Requirements

### Requirement: Strip proxy identity headers before upstream forwarding
Before forwarding requests to the upstream Responses endpoint, the service MUST strip network/proxy identity headers derived from downstream edges. The service MUST remove `Forwarded`, `X-Forwarded-*`, `X-Real-IP`, `True-Client-IP`, and `CF-*` headers, and MUST continue to set upstream auth/account headers from internal account state.

#### Scenario: Request contains reverse-proxy forwarding headers
- **WHEN** the inbound request includes headers such as `X-Forwarded-For`, `X-Forwarded-Proto`, `Forwarded`, or `X-Real-IP`
- **THEN** those headers are not forwarded to upstream

#### Scenario: Request contains Cloudflare identity headers
- **WHEN** the inbound request includes headers such as `CF-Connecting-IP` or `CF-Ray`
- **THEN** those headers are not forwarded to upstream
