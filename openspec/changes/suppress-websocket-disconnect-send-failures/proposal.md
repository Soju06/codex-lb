## Why

Live `codex-lb-direct` showed an `Exception in ASGI application` from the
Responses websocket connect-failure path. The client disconnected before the
proxy could send the generated error frame, so Starlette raised
`WebSocketDisconnect` from the final downstream `send_text`.

The request cleanup and observability work had already run before the send.
The remaining gap was treating a disappeared client as an expected terminal
condition instead of letting uvicorn log it as an application exception.

## What Changes

- keep websocket connect-failure cleanup, reservation release, gate release,
  and request-log persistence unchanged
- swallow expected client disconnects only around the final connect-failure
  error-frame send
- add focused unit coverage for the disconnect-after-cleanup path

## Impact

Expected client disconnects during pre-commit websocket failure reporting no
longer produce noisy ASGI exceptions. Real upstream connect failures are still
recorded in request logs and surfaced to connected clients when possible.
