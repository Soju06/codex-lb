## ADDED Requirements

### Requirement: Compact upstream requests use minimal transport headers
When the service forwards `/backend-api/codex/responses/compact` or `/v1/responses/compact` to the upstream compact endpoint, it MUST construct the upstream header set from internal credentials and selected account identity rather than forwarding downstream client transport headers. The upstream compact request MUST include `Authorization`, `Accept: application/json`, `Content-Type: application/json`, and `chatgpt-account-id` when an upstream account id is available. It MUST NOT forward downstream `Content-Encoding`, `Content-Length`, `Connection`, `Keep-Alive`, `Proxy-*`, `TE`, `Trailer`, `Transfer-Encoding`, `Upgrade`, or client identity headers such as Codex session and OpenAI client version headers on the final upstream compact request.

#### Scenario: compact request arrives with downstream transport headers
- **WHEN** a client sends a compact request with downstream body-framing, compression, hop-by-hop, or client identity headers
- **THEN** the service selects and routes the request using the existing compact affinity rules
- **AND** the final upstream compact request includes only internal auth, JSON accept/content type, and selected account identity headers
- **AND** the final upstream compact request omits the downstream transport and client identity headers
