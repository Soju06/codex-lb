## Context

`codex-lb` accepts OpenAI-style service-tier values and forwards Responses
traffic to the ChatGPT/Codex upstream. Live logs showed the upstream rejecting a
request-side `service_tier: "default"` with `invalid_request_error`. That value
is semantically the absence of a special tier, but forwarding it as a literal
request field can terminate an HTTP bridge session and weaken subsequent
`previous_response_id` continuity.

## Goals / Non-Goals

**Goals:**

- Remove request-side `service_tier: "default"` from outbound upstream payloads.
- Preserve local request state so accounting can still treat the request as the
  default tier when upstream omits an actual tier.
- Leave existing `fast` to `priority` canonicalization and literal `ultrafast`
  forwarding untouched.

**Non-Goals:**

- Do not change API-key enforcement semantics.
- Do not change response parsing for upstream-reported `service_tier: "default"`.
- Do not alter bridge routing or `previous_response_id` retry behavior directly.

## Decisions

- Implement the sanitation in the shared Responses payload serializer rather
  than at a single route. This keeps HTTP, websocket, v1, backend, and compact
  callers consistent because all upstream paths already call `to_payload()`.
- Strip only the literal default tier. Alternatives such as rejecting the client
  payload or remapping `default` to another tier were rejected because `default`
  is a no-op request preference, not a client error or fast-tier request.

## Risks / Trade-offs

- [Risk] A future upstream may accept request-side `default` explicitly.
  Mitigation: omitting it remains semantically equivalent to default-tier
  execution.
- [Risk] Local logs could lose the requested tier if sanitation mutates the
  Pydantic model. Mitigation: the change only removes the serialized upstream
  field and leaves `request.service_tier` unchanged.
