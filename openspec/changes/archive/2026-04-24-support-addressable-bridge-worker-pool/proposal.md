# support-addressable-bridge-worker-pool

## Why

The HTTP responses session bridge keeps live upstream websocket sessions in process-local memory. A plain Uvicorn multi-worker process group is unsafe because sibling workers share one advertised bridge instance id and one listener endpoint while holding separate in-memory bridge sessions. The current guard preserves correctness by forcing one worker, but it leaves multi-core headroom unused on direct Docker installs.

## What Changes

- replace bridge-enabled Uvicorn multi-worker startup with an addressable bridge worker pool
- run each bridge worker as a single-worker app process with a unique bridge instance id and loopback endpoint
- run a front listener that load-balances public HTTP and WebSocket traffic across those worker endpoints
- keep plain Uvicorn multi-worker serving available when the HTTP responses session bridge is disabled
- let direct Docker use the configured worker count without duplicating the env-file worker value

## Impact

- bridge-backed Codex sessions can use multiple local CPU cores without sharing one in-memory bridge owner identity
- direct Docker keeps one exposed service port and the image startup contract
- owner handoff remains explicit through the bridge ring and worker-specific advertised endpoints
- no public API contract change
