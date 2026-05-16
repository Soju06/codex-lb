### MODIFIED Requirements

### Requirement: HTTP Responses routes preserve upstream websocket session continuity
When serving HTTP `/v1/responses` or HTTP `/backend-api/codex/responses`, the service MUST preserve upstream Responses websocket session continuity on a stable per-session bridge key instead of opening a brand new upstream session for every eligible request. The bridge key MUST use an explicit session/conversation header when present; otherwise it MUST use normalized `prompt_cache_key`, and when the client omits `prompt_cache_key` the service MUST derive a stable key from the same cache-affinity inputs already used for OpenAI prompt-cache routing. While bridged, the service MUST preserve the external HTTP/SSE contract, MUST continue request logging with `transport = "http"`, and MUST keep requests from different bridge keys isolated from one another.

#### Scenario: bridge forwards hard continuity keys to the owner replica
- **WHEN** operators configure multiple eligible bridge instance ids
- **AND** a request uses a bridge key derived from `x-codex-turn-state` or an explicit session header
- **AND** that request lands on a non-owner instance
- **THEN** the service MUST forward the request internally to the owner replica
- **AND** it MUST NOT return a topology-bearing `bridge_instance_mismatch` error to the client for that owner mismatch alone

#### Scenario: gateway-style prompt-cache bridge requests tolerate wrong-replica arrival
- **WHEN** a request uses a bridge key derived only from `prompt_cache_key` or a derived prompt-cache key
- **AND** that request lands on a non-owner instance
- **THEN** the service MAY create or reuse a local bridge session on that instance
- **AND** it MUST treat the owner mismatch as a locality miss instead of a continuity failure

#### Scenario: forwarded bridge requests fail closed when owner forwarding loops
- **WHEN** a forwarded hard-continuity bridge request reaches another non-owner replica
- **THEN** the service MUST fail the request with a generic 5xx bridge-forward error
- **AND** it MUST NOT attempt another owner handoff
