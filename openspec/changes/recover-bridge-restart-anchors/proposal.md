## Why

A killed codex-lb process can leave durable HTTP bridge rows that still look
owned by the replacement process when Docker restarts the same container id.
Those stale rows keep previous-response and session anchors addressable, so
clients reconnect to dead continuity state and receive retryable
`stream_idle_timeout` semantics instead of a fast recovery boundary.

## What Changes

- Persist a per-process bridge owner epoch alongside the existing instance id
  and owner fencing epoch.
- On startup, retire rows owned by a previous process epoch for the same
  instance id and remove their attachable aliases.
- Treat continuity-bound idle terminals as non-retryable fresh-turn guidance
  only when durable ownership is proven dead; ordinary upstream silence keeps
  the existing retryable timeout behavior.
- Poison a durable bridge anchor after repeated zero-event idle failures on
  the same hard bridge key, using the existing retry-circuit count and capping
  admission-waiter retirement deferral.

## Impact

- Affected capabilities: `responses-api-compat`.
- Existing durable owner fencing remains intact; the process epoch only
  distinguishes process incarnations under a stable instance id.
- No live deployment or live database mutation is part of this change.
