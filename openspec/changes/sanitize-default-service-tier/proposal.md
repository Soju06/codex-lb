## Why

The ChatGPT/Codex upstream rejects `service_tier: "default"` on `/codex/responses`
with `invalid_request_error`, even though clients can reasonably send that value
to mean the ordinary tier. In bridge-backed Codex traffic, that upstream rejection
can terminate the live bridge session and make the next incremental turn more
likely to hit `previous_response_not_found`.

## What Changes

- Treat request-side `service_tier: "default"` as a no-op before forwarding
  Responses payloads upstream.
- Keep other service-tier values unchanged, including `priority`, `flex`,
  `auto`, and `ultrafast`.
- Preserve local response/request-log semantics that can still record
  upstream-reported `service_tier: "default"`.

## Impact

- Prevents avoidable upstream `Unsupported service_tier: default` failures.
- Reduces bridge continuity churn that can cascade into
  `previous_response_id` failures on later turns.
- Does not change API-key enforced tiers or downstream response formatting.
