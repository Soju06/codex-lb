## Why

Current `main` bounds an HTTP bridge request that receives no
`response.created` acknowledgement, but only after 240 seconds and by failing
the client request. Production evidence on issue #1393 shows that an immediate
fresh attempt commonly succeeds, so the proxy exposes a long avoidable failure
instead of using its existing pre-visible replay path.

## What Changes

- Reduce the eventless pre-`response.created` watchdog cap from 240 seconds to
  30 seconds.
- Detect a warm upstream socket that is already closed before a new HTTP-bridge
  or direct-WebSocket send starts and transparently reconnect/resend once
  without exposing a client reconnect.
- On the first eventless timeout, cancel the old receive wait and attempt one
  replay through the existing pre-created replay guards and fresh-socket
  reconnect path.
- Process an upstream event that wins the receive-cancellation race instead of
  discarding it, and keep the replay on the account whose concurrency lease the
  request already holds.
- Continue the original downstream stream when replay succeeds.
- Preserve the current account-neutral terminal settlement and whole-session
  retirement when replay is unsafe, reconnect/resend fails, or the replay also
  misses `response.created`.
- Keep hard-affinity and file-backed work on its required account and retain the
  existing no-replay boundary after response lifecycle or downstream-visible
  progress.
- Keep send errors after dispatch may have begun fail closed; only the
  adapter's sealed pre-dispatch proof permits an immediate exact resend.

## Capabilities

### Modified Capabilities

- `proxy-admission-control`: Recover one safely replayable eventless gate owner
  before retiring the bridge.
- `responses-api-compat`: Keep the retry transparent and bounded before any
  response lifecycle or downstream-visible output.

## Impact

- Affected code: websocket send classification, HTTP bridge and direct
  WebSocket request submission, eventless deadline, and upstream-reader timeout
  handling.
- Affected surface: streaming Responses requests served through the HTTP bridge
  and direct WebSocket proxy.
- No new setting, dependency, endpoint, schema, migration, account-health
  penalty, or durable coordinator.
- This partially addresses #1393. Cross-request cooldown and eventful
  missing-created recovery remain separate work.
