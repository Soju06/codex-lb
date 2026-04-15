## MODIFIED Requirements

### Requirement: HTTP Responses routes preserve upstream websocket session continuity
When serving HTTP `/v1/responses` or HTTP `/backend-api/codex/responses`, the service MUST preserve upstream Responses websocket session continuity on a stable per-session bridge key instead of opening a brand new upstream session for every eligible request. The bridge key MUST use an explicit session/conversation header when present; otherwise it MUST use normalized `prompt_cache_key`, and when the client omits `prompt_cache_key` the service MUST derive a stable key from the same cache-affinity inputs already used for OpenAI prompt-cache routing. While bridged, the service MUST preserve the external HTTP/SSE contract, MUST continue request logging with `transport = "http"`, and MUST keep requests from different bridge keys isolated from one another.

#### Scenario: HTTP previous_response_id remains valid within a bridged /v1 session
- **WHEN** a client sends a later HTTP `/v1/responses` request with `previous_response_id` that references a response created earlier on the same bridged session
- **THEN** the service forwards that request through the same upstream websocket session so upstream can resolve the referenced prior response

#### Scenario: active bridged continuation retries once after upstream drop
- **WHEN** an HTTP `/v1/responses` or `/backend-api/codex/responses` request includes `previous_response_id`
- **AND** the matching bridged session still exists locally
- **AND** the upstream websocket drops before `response.created` arrives for that request
- **THEN** the service reconnects the bridged session once with continuity headers intact
- **AND** it replays the pending request on that fresh upstream websocket instead of waiting for the idle timeout

#### Scenario: active bridged continuation surfaces upstream failure after reconnect retry
- **WHEN** an HTTP continuation request with `previous_response_id` exhausts its single fresh-upstream reconnect attempt
- **THEN** the service fails the request as an upstream availability error
- **AND** it MUST NOT rewrite that failure to `previous_response_not_found` solely because the active upstream session dropped mid-request

#### Scenario: HTTP previous_response_id fails closed when bridged continuity is unavailable before submission
- **WHEN** a client sends HTTP `/v1/responses` or `/backend-api/codex/responses` with `previous_response_id`
- **AND** there is no matching live bridged upstream websocket session for that continuity key before the request is submitted upstream
- **THEN** the service MUST fail the request without opening a fresh unrelated upstream session
- **AND** it MUST return `previous_response_not_found` on `previous_response_id`
