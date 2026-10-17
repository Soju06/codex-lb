## Context

WebSocket create applies API-key enforcement and rewrites accepted `ultra` to
the outbound `max` alias before building the request state. Steering currently
reconstructs policy from that serialized outbound frame and invents `medium`
when the original request omitted effort.

## Decision

Keep the effective pre-wire effort on the existing Astra request-state field.
At steering retention, fold configuration updates over that value; absent
effort stays absent. Validate the resulting continuation against the refreshed
key and continue sending only the existing upstream steering frame.

Remove the independent count cap; queued bytes already bound admitted input.
Specify the existing aiohttp dispatch-seam failure as a local 503 before
upstream dispatch. Keep the one parse at completion that strips historical
input: deferring it would retain the body until a possible future steer,
while eagerly copying it would restore the previously rejected extra payload.

## Verification

Run the real ASGI WebSocket route against a local upstream WebSocket for
missing, client-ultra, enforced-ultra, and refreshed-forbidden effort; run
the actual aiohttp adapter with a missing transport seam, plus scoped tests,
diagnostics, build and pinned OpenSpec validation.
