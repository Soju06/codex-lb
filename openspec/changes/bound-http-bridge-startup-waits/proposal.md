# Change: Bound HTTP Bridge Startup Waits

## Why

Production observed HTTP Responses bridge streams that emitted only local keepalive comments for minutes while direct upstream requests and fresh in-process bridge calls succeeded. Restarting the replica cleared the stuck in-memory state.

The bridge currently has unbounded waits before the first upstream `response.*` event: waiting for per-session response-create gate capacity, waiting for global bridge capacity to be freed by an in-flight session, and waiting for another request's in-flight session creation. If one of these waits wedges, the API startup probe can return a streaming response and downstream clients only see keepalives until their own watchdog aborts.

## What Changes

- Bound HTTP bridge startup waits with the configured proxy admission wait timeout.
- Clean up in-flight bridge session creation futures when the owner request is cancelled while closing stale sessions.
- Evict stale in-flight bridge session futures after startup wait timeout so a replica can self-heal without restart.
- Return a local `proxy_overloaded` error when bridge startup admission cannot proceed in time.
- Log the timeout stage and request id without exposing raw affinity keys.
- Add regression coverage for session gate, capacity wait, and in-flight session wait timeouts.

## Impact

Affected clients receive a terminal local-overload error after the configured timeout instead of a keepalive-only stream that can last until a 300s client watchdog aborts. A stuck in-flight bridge session marker is also removed so later requests can create a fresh bridge session instead of repeatedly waiting on the same orphaned future.
