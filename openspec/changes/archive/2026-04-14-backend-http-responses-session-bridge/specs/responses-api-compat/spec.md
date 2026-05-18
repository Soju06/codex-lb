## MODIFIED Requirements

### Requirement: HTTP Responses routes preserve upstream websocket session continuity
HTTP `/backend-api/codex/responses` MUST share the same persistent upstream websocket bridge behavior as HTTP `/v1/responses`, including stable bridge-key reuse, `previous_response_id` continuity within a live bridged session, and external request logging with `transport = "http"`.


### Requirement: HTTP responses emit reusable turn-state headers
HTTP `/backend-api/codex/responses` and HTTP `/v1/responses` MUST return an `x-codex-turn-state` response header so clients can replay it on later requests to gain Codex-session continuity.

### Requirement: HTTP previous_response_id fails closed when bridge continuity is gone
HTTP `/backend-api/codex/responses` and HTTP `/v1/responses` MUST NOT open a fresh upstream websocket session for requests that already depend on `previous_response_id`. When the matching live bridged session no longer exists, the service MUST fail closed with `previous_response_not_found` on `previous_response_id` instead of silently forking continuity onto a new upstream session.
