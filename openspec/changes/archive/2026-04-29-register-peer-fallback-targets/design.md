## Context

Peer fallback already forwards eligible pre-output HTTP/SSE failures to configured peer `codex-lb` base URLs. The current target list comes only from `CODEX_LB_PEER_FALLBACK_BASE_URLS`, so changing peers requires deployment-level configuration access and typically a restart. The dashboard already manages operational resources such as accounts, API keys, firewall rules, and sticky sessions through module-scoped repository/service/API patterns.

## Goals / Non-Goals

**Goals:**

- Let dashboard users register, enable/disable, update, and delete peer fallback base URLs.
- Keep environment-variable peer fallback working for existing deployments.
- Prefer enabled database-registered targets when any exist so operators can move peer management out of env.
- Preserve existing fallback eligibility, loop-prevention markers, peer health checks, and HTTP/SSE-only behavior.

**Non-Goals:**

- Implement websocket peer fallback.
- Replace external process-down failover or Kubernetes/service-mesh failover.
- Add long-term health history, automatic target discovery, or per-target authentication policy in this change.

## Decisions

- **Use a dedicated `peer_fallback_targets` table.** A separate table matches the account-like management model better than packing a list into dashboard settings. It allows stable IDs, enabled flags, unique URL enforcement, timestamps, and future metadata without rewriting a singleton settings row.
- **Resolve runtime targets from DB first, env second.** If any database target rows exist, peer fallback uses the enabled subset of those rows. If no database target rows exist, the existing environment list remains the effective bootstrap/default list. This keeps current deployments compatible while letting dashboard-managed peers take over once registered.
- **Keep runtime lookup lazy.** Peer fallback target resolution only happens after a local request has already become eligible for fallback, avoiding extra database work on successful proxy requests.
- **Add a Settings section instead of a new top-level page.** Peer fallback is a routing/resilience control, so placing it near routing settings keeps the dashboard navigation compact and consistent with existing operational settings.

## Risks / Trade-offs

- **DB lookup during failure path** -> fallback now performs a database read before trying peers. This is limited to failure cases and can fall back to env targets if no DB targets exist.
- **Deleting all DB targets reverts to env defaults** -> the dashboard can disable registered targets, but deleting every registered target restores environment-configured fallback behavior. Operators who want env disabled must remove the env setting, preserving backward compatibility.
- **No persistent health state** -> the UI only manages configuration; runtime still checks `/health` before forwarding. This avoids stale dashboard health indicators in the first iteration.
