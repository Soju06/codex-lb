## Why

An upstream HTTP bridge websocket can close while the next request is already
waiting on the session response-create gate. The reader currently retires the
session before that unsent request acquires the gate, so the request receives an
internal `HTTP responses session bridge is closed` error even though it can be
safely sent once on a fresh same-session upstream connection.

## What Changes

- Track unsent admission waiters explicitly.
- Defer session retirement and pruning while a waiter owns the handoff.
- Reconnect the retained same-account session before sending the waiter once.
- Retire the closed session when its last waiter is cancelled or fails.

## Impact

Long Codex turns can continue across the upstream-close/admission race without
exposing an internal bridge lifecycle error. Unsafe or ambiguous sends remain
fail-closed.
