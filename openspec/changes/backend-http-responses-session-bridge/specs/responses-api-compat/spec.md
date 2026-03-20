## MODIFIED Requirements

### Requirement: HTTP Responses routes preserve upstream websocket session continuity
HTTP `/backend-api/codex/responses` MUST share the same persistent upstream websocket bridge behavior as HTTP `/v1/responses`, including stable bridge-key reuse, `previous_response_id` continuity within a live bridged session, and external request logging with `transport = "http"`.


### Requirement: HTTP responses emit reusable turn-state headers
HTTP `/backend-api/codex/responses` and HTTP `/v1/responses` MUST return an `x-codex-turn-state` response header so clients can replay it on later requests to gain Codex-session continuity.
