# responses-api-compat Delta

## MODIFIED Requirements

### Requirement: Continuity-dependent Responses follow-ups fail closed with retryable errors
When a Responses follow-up depends on previously established continuity state, the service MUST return a retryable continuity error if that continuity cannot be reconstructed safely. The service MUST NOT expose raw `previous_response_not_found` for bridge-local metadata loss or similar internal continuity gaps. When forwarding a turn-state-anchored follow-up to its bridge owner fails with `bridge_owner_unreachable` and a fresh durable lookup shows the owner no longer holds an active lease (released, expired, or the row is missing or CLOSED), the service MUST recover the follow-up locally through durable takeover instead of returning the retryable error. The fresh durable lookup MUST use the same resolution semantics as request routing, including the latest-turn-state fallback, so a row originally resolved without a registered alias remains takeover-eligible. When the durable lease is still actively held by another instance — including DRAINING rows whose lease has not been released or expired — the service MUST keep failing closed with the retryable error.

#### Scenario: HTTP bridge loses local continuity metadata for a follow-up request
- **WHEN** an HTTP `/v1/responses` or `/backend-api/codex/responses` follow-up request depends on `previous_response_id` or a hard continuity turn-state
- **AND** the bridge cannot reconstruct the matching live continuity state from local or durable metadata
- **THEN** the service returns a retryable OpenAI-format error
- **AND** the error code is not `previous_response_not_found`

#### Scenario: in-flight bridge follower loses continuity while waiting on the same canonical session
- **WHEN** a follow-up request waits on an in-flight HTTP bridge session for the same hard continuity key
- **AND** the bridge still cannot reconstruct safe continuity state once the leader finishes
- **THEN** the service returns a retryable OpenAI-format error
- **AND** the error code is not `previous_response_not_found`

#### Scenario: multiplexed follow-ups fail closed only for the matching continuity anchor
- **WHEN** a websocket or HTTP bridge session has multiple pending follow-up requests with different `previous_response_id` anchors
- **AND** continuity loss is detected for exactly one of those anchors
- **THEN** the service applies the retryable fail-closed continuity error only to the matching follow-up request
- **AND** it does not expose raw `previous_response_not_found`
- **AND** unrelated pending requests continue on their own response lifecycle

#### Scenario: multiplexed follow-ups sharing one anchor fail closed together without leaking raw continuity errors
- **WHEN** a websocket or HTTP bridge session has multiple pending follow-up requests that share the same `previous_response_id` anchor
- **AND** upstream emits an anonymous continuity loss event such as `previous_response_not_found` for that shared anchor
- **THEN** the service rewrites each affected follow-up into a retryable continuity error
- **AND** no affected follow-up exposes raw `previous_response_not_found`
- **AND** the run remains usable for subsequent requests after the rewritten failures

#### Scenario: single pre-created follow-up still fails closed when continuity loss omits explicit response id in message
- **WHEN** a websocket follow-up request is pending with `previous_response_id` and has not received a stable upstream `response.id` yet
- **AND** upstream emits `previous_response_not_found` with `param=previous_response_id`
- **AND** the upstream error message omits the literal previous response identifier
- **THEN** the service still maps that continuity loss to the pending follow-up
- **AND** it rewrites the downstream terminal event to a retryable continuity error
- **AND** it does not surface raw `previous_response_not_found` to the client

#### Scenario: turn-state follow-up recovers locally after the owner released its lease
- **WHEN** a turn-state-anchored follow-up without `previous_response_id` is forwarded to its bridge owner during the post-shutdown ring grace window
- **AND** the forward fails with `bridge_owner_unreachable`
- **AND** a fresh durable lookup using the request-routing resolution semantics (registered alias or latest-turn-state fallback) shows the lease is released or expired
- **THEN** the service retries the follow-up locally through durable takeover instead of returning the retryable 503
- **AND** the takeover retry carries the fresh durable lookup as its continuity anchor even when the turn-state alias registration was lost
- **AND** a fresh durable lookup showing a live lease held by another instance — even for a DRAINING row — still fails closed with the retryable `bridge_owner_unreachable` error
