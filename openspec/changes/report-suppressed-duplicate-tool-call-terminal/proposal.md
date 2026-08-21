# Report suppressed duplicate tool-call terminals

## Why

A duplicate side-effect tool-call replay cannot safely continue, but every
transport still needs to settle the request and report the same retryable
outcome.

## What Changes

- Emit a specific failed terminal instead of misclassifying the replay as an
  incomplete upstream stream.
- Keep settlement, durable operation persistence, and account-health fencing
  aligned across direct SSE, the HTTP bridge, and WebSocket clients.
