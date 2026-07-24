## Why

A durable HTTP Responses continuation can be rebuilt after its original local
bridge has closed. Sending the restored `previous_response_id` as the first
`response.create` on a fresh upstream WebSocket can leave upstream completely
silent, so interactive clients wait for the 240-second eventless watchdog and
then repeat the same reattach.

The streaming route also leaves its first-item probe task alive when response
teardown wins before the response body is iterated. That prevents the bridge
generator's cancellation cleanup from running until the upstream watchdog
eventually retires the request.

## What Changes

- Project proxy-injected fresh-bridge continuations into the existing verified
  account-neutral full-resend shape before their first upstream send.
- Give a verified client-supplied fresh-bridge full resend one bounded recovery
  without `previous_response_id` when the anchored attempt remains eventless.
- Keep unsafe, owner-bound, or incomplete continuation payloads fail-closed and
  preserve the existing no-replay rule after ambiguous delivery.
- Tie the startup first-item probe to the ASGI response lifecycle so teardown
  closes the source even when body iteration never begins.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `responses-api-compat`: fresh durable reattach avoids repeating a poisoned
  WebSocket anchor while preserving replay safety.
- `proxy-admission-control`: verified fresh reattach receives a shorter bounded
  startup deadline and response teardown releases bridge ownership promptly.

## Impact

- Code: HTTP Responses bridge request preparation, eventless recovery, and
  streaming response lifecycle ownership.
- Tests: replay-safety unit coverage plus externally visible `/v1/responses`
  bridge and cancellation regressions.
- Specs: `responses-api-compat` and `proxy-admission-control`.
