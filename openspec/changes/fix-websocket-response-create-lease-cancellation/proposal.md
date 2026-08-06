# Change: Make WebSocket response-create lease cleanup cancellation-safe

## Why

WebSocket terminal cleanup clears the request state's account response-create
lease before awaiting its asynchronous release. Cancellation at that await can
leave the account slot counted until stale-lease reclamation.

## What Changes

- Shield the account response-create lease release in WebSocket gate cleanup.
- Add regression coverage for cancellation under load-balancer runtime-lock
  contention and retain coverage for genuine stale-lease reclamation.
