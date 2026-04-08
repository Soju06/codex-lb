# Upstream Provider Management Context

## Purpose and Scope

This capability defines how the dashboard and API manage provider-aware upstream identities, including ChatGPT-web accounts and the phase-1 OpenAI Platform fallback identity.

See `openspec/specs/upstream-provider-management/spec.md` for normative requirements.

## Decisions

- `chatgpt_web` remains the primary upstream for existing behavior.
- `openai_platform` is fallback-only in phase 1 and is intentionally narrow in scope.
- Phase 1 mixed-provider mode supports only one Platform API key.
- Provider-aware routing is explicit rather than treating Platform as an equal-weight member of the ChatGPT pool.

## Supported Fallback Routes

- `GET /v1/models`
- stateless HTTP `POST /v1/responses`

## Unsupported Platform-backed Routes in Phase 1

- `/backend-api/codex/*`
- downstream websocket `/responses`
- downstream websocket `/v1/responses`
- `/v1/responses/compact`
- `/backend-api/codex/responses/compact`
- `/v1/chat/completions`
- continuity-dependent requests using `conversation`, `previous_response_id`, `session_id`, `x-codex-session-id`, `x-codex-conversation-id`, or `x-codex-turn-state`

## Fallback Policy

- ChatGPT accounts remain primary whenever at least one compatible `chatgpt_web` candidate is below both configured drain thresholds.
- Platform fallback is allowed only when every compatible ChatGPT candidate is at or above either the primary or secondary drain threshold.
- Credits are not part of the fallback decision.

## Operational Constraints

- Platform fallback requires at least one active ChatGPT account.
- A Platform identity can be registered with zero eligible route families; in that state it remains unroutable until the operator opts into a supported route family.
- Repeated upstream `401` or `403` failures should deactivate the Platform identity until the operator repairs or re-enables it.

## UX Expectations

- The dashboard should describe Platform identities as fallback-only.
- Route-family labels should clarify that `public_responses_http` means stateless HTTP `/v1/responses` only.
- Operators should not expect compact, websocket, or ChatGPT-private behavior from the Platform path in phase 1.
