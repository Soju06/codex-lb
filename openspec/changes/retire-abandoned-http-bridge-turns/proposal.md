## Why

HTTP Responses bridge sessions serialize active upstream turns for a client
session. If a downstream client disconnects after visible output has started but
before the upstream turn reaches a terminal event, the orphaned turn can keep
the bridge admission slot and account stream lease held until stale cleanup runs.
Same-session retries then wait for the held slot and time out even though the
account remains healthy for unrelated sessions.

## What Changes

- Detect downstream disconnects for bridge-routed streaming Responses requests.
- Close the wrapped SSE stream chain so disconnect cleanup reaches the existing
  HTTP bridge request detach path.
- Retire the abandoned upstream bridge turn instead of replaying post-visible
  output.

## Impact

- Affects `/v1/responses` and `/backend-api/codex/responses` traffic using the
  HTTP responses session bridge.
- Does not change replay policy: post-visible interruptions still fail closed and
  are not retried locally.
