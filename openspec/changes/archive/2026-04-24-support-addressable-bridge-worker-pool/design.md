## Context

The HTTP responses session bridge keeps live upstream websocket sessions in process-local memory. A plain Uvicorn multi-worker process group is unsafe for bridge-enabled serving because sibling workers can share one public listener and one apparent bridge identity while holding different in-memory session maps. The existing fail-closed guard preserved correctness by forcing one worker whenever the bridge was enabled, but that also prevented direct Docker installs from using available CPU cores.

Direct Docker must keep the existing single public service port and image startup contract. The worker-pool mode therefore needs to be internal to the container: one public listener accepts client traffic, while private loopback worker processes own the bridge sessions.

## Goals / Non-Goals

**Goals:**

- Allow bridge-enabled direct Docker installs to use worker counts above one without sharing one bridge owner identity across independent process memory.
- Preserve the public listener shape on `:2455`.
- Keep bridge owner handoff explicit through per-worker advertised endpoints.
- Preserve plain Uvicorn multi-worker startup when the HTTP responses session bridge is disabled.
- Keep the direct Docker helper aligned with `.env.local` instead of adding a duplicate worker override.

**Non-Goals:**

- Change the public HTTP or WebSocket API contract.
- Replace the bridge ring membership model.
- Add cross-process sharing for live websocket session maps.
- Change orchestrated multi-replica deployment topology outside the direct Docker worker-pool path.

## Decisions

1. Use a front listener plus single-worker backend app processes for bridge-enabled worker counts above one.

   The front listener keeps the existing public port stable while each backend process runs the normal app with `--workers 1`. This avoids shared bridge identity over separate process-local session maps. The alternative was plain Uvicorn multi-worker startup with a static ring, which is the unsafe configuration this change replaces.

2. Give each backend a unique bridge instance id and loopback advertised endpoint.

   Worker ids are derived from the base bridge instance id with a `-worker-N` suffix. Endpoints use loopback ports derived from the public port. This makes owner handoff addressable inside the container without exposing extra ports. The alternative was to keep one advertised endpoint at the front listener, but that cannot target a worker-local session map.

3. Keep loopback advertised endpoints allowed only in worker-pool mode.

   Loopback endpoints are unsafe for independent replicas because peers cannot reach another host's loopback. In direct Docker worker-pool mode, all bridge workers share the same network namespace, so loopback is the intended routing surface. The validator therefore keeps the existing fail-closed behavior except when the explicit worker-pool flag is set.

4. Remove stale endpoint owners when a worker claims an advertised endpoint.

   Direct container restarts reuse worker ports. If old worker rows remain active for the stale heartbeat window, owner selection can choose an old instance id whose endpoint now points at a new worker. Registration and heartbeat delete other ring rows with the same advertised endpoint before upserting the current worker.

5. Stabilize the direct container hostname.

   The default bridge instance id comes from the hostname. Setting the direct container hostname to its stable container name keeps worker ids stable across rebuilds and reduces unnecessary ring churn. The endpoint conflict cleanup still protects restarts even when stale rows exist.

## Risks / Trade-offs

- Front listener becomes an extra in-container hop -> mitigated by loopback routing and streaming request/response forwarding.
- Worker-pool mode adds more child processes to supervise -> mitigated by terminating all backend processes when the front process exits or any worker exits unexpectedly.
- Port collisions are possible if the derived backend port range is already occupied -> mitigated by using loopback-only ports derived from the public port and allowing `CODEX_LB_BRIDGE_WORKER_BASE_PORT` override.
- Old durable ring rows can briefly exist after restart -> mitigated by endpoint-owner conflict cleanup during registration and heartbeat.

## Migration Plan

1. Deploy the runtime code and direct Docker helper changes.
2. Rebuild and recreate `codex-lb-direct` with the existing `.env.local`.
3. Confirm the front listener reports a bridge ring containing the configured worker count.
4. Confirm each backend loopback endpoint reports ready.
5. Check recent logs for owner-forward failures.

Rollback is to set `CODEX_LB_UVICORN_WORKERS=1` and recreate the direct container, or disable the HTTP responses session bridge if plain Uvicorn multi-worker serving is required.

## Open Questions

None for this change. Future work can tune front-proxy load balancing policy separately if request distribution needs to consider worker health beyond readiness.
