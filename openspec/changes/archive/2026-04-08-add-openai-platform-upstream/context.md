## Rollout and Operator Caveats

Phase 1 keeps `chatgpt_web` as the primary upstream for all existing behavior. `openai_platform` is a fallback-only provider and is intentionally narrow in scope.

### Supported fallback routes

- `GET /v1/models`
- stateless HTTP `POST /v1/responses`

### Unsupported Platform-backed routes in phase 1

- `/backend-api/codex/*`
- downstream websocket `/responses`
- downstream websocket `/v1/responses`
- `/v1/responses/compact`
- `/backend-api/codex/responses/compact`
- `/v1/chat/completions`
- continuity-dependent requests using `conversation`, `previous_response_id`, `session_id`, `x-codex-session-id`, `x-codex-conversation-id`, or `x-codex-turn-state`

### Fallback policy

- ChatGPT accounts remain primary whenever at least one compatible `chatgpt_web` candidate is below both configured drain thresholds.
- Platform fallback is allowed only when every compatible ChatGPT candidate is at or above either the primary or secondary drain threshold.
- Credits are not part of the fallback decision.

### Operational constraints

- Platform fallback requires at least one active ChatGPT account.
- Only one Platform API key may exist in phase 1.
- A Platform identity can be registered with zero eligible route families; in that state it remains unroutable until the operator opts into a supported route family.

### UX expectations

- The dashboard should describe Platform identities as fallback-only.
- Route-family labels should clarify that `public_responses_http` means stateless HTTP `/v1/responses` only.
- Operators should not expect compact, websocket, or ChatGPT-private behavior from the Platform path in phase 1.
