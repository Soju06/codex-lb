## Why

The HTTP Responses bridge currently opens its retry circuit after a clean
upstream WebSocket close even when the replacement socket also closes before
producing any response event. A downstream idle-recovery task can also replace
the upstream socket without restarting its reader. Closing the old socket then
wakes that stale reader, which misclassifies the proxy-initiated close as an
upstream failure and retires work already moved to the replacement socket.
Together these behaviors make a transient handoff issue visible as a reconnect
loop and require the Codex client to be restarted.

## What Changes

- Permit one additional pre-visible replay when the replacement upstream
  WebSocket closes cleanly before any response event.
- Add bounded, configurable jitter before that additional replay to avoid
  synchronized reconnects.
- Emit a dedicated diagnostic event for the additional clean-close replay.
- Keep the allowance hard-capped at one and preserve all existing no-replay
  behavior after downstream-visible output or continuity-sensitive state.
- When recovery is initiated outside the upstream reader, cancel and await the
  old reader before closing its socket, then start exactly one reader for the
  replacement socket.
- Keep the shared session live while the replacement socket opens so concurrent
  idle pruning cannot evict and fail its pending response during the handoff.
- Start silent pre-response recovery with enough headroom to reconnect before
  the downstream client's request timeout boundary.
- Do not let a proxy-initiated close of a superseded socket retire pending work
  on the replacement socket or increment the retry circuit.

## Impact

- Repeated clean handoffs can recover transparently without an immediate
  terminal circuit-open response.
- The retry remains bounded and does not create an unbounded replay loop.
- Reader ownership follows the active socket across idle recovery, preventing
  locally generated close frames from being counted as upstream instability.
- Adds the `http_bridge_retry_circuits` durable table and migration so retry
  cooldown state survives cross-replica clean-close and incomplete-stream
  failures.
