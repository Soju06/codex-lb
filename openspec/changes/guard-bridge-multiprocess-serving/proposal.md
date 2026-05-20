# guard-bridge-multiprocess-serving

## Why

The HTTP responses session bridge keeps active upstream websocket sessions in process-local memory while durable bridge metadata records ownership at the configured `http_responses_session_bridge_instance_id`. A single Docker container running multiple Uvicorn worker processes shares one hostname-derived instance id, but each worker has a separate in-memory session map. That makes a continuation request unsafe when it lands on a sibling worker: durable ownership can appear local even though the actual bridge session is in another process.

The direct Docker fallback also documented an explicit command override. That bypassed the image startup script, including the single migration handoff before application workers start.

## What Changes

- fail closed when more than one Uvicorn worker is configured while the HTTP responses session bridge is enabled
- keep multi-worker serving available only when the bridge is disabled
- make the documented direct Docker fallback use the image default startup script
- make the local direct Docker helper rebuild the current checkout and recreate `codex-lb-direct` without mutating git state
- force the direct Docker fallback to a bridge-safe worker count by default

## Impact

- bridge-backed Codex sessions prefer correctness over process-level throughput
- higher worker counts remain possible for deployments that disable the HTTP responses session bridge
- Docker startup keeps migration execution centralized in the image startup script
- local rebuild/update behavior matches the live `codex-lb-direct` deployment shape
