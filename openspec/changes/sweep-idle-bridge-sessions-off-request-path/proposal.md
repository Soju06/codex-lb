## Why

The HTTP-bridge idle sweep (`_prune_http_bridge_sessions_locked`) is reached from exactly one place: `_get_or_create_http_bridge_session`. It is therefore request-driven, so a replica that stops receiving bridge requests never evicts its idle sessions and holds their upstream WebSockets, registry entries, and durable claims until the process restarts.

This is the residual leg of issue #1354. Fix (a) (#1476) already made account stream leases turn-scoped, so an idle session releases its cap slot as soon as its last turn detaches — the cap-exhaustion symptom is addressed there. What remains is resource hygiene on a quiet replica: in a multi-replica deployment, traffic moving off one replica leaves its warm sessions pinned indefinitely.

## What Changes

- Expose the existing sweep as `prune_idle_http_bridge_sessions()`: take the bridge lock, run the same `_prune_http_bridge_sessions_locked` selection the request path uses, and schedule the pruned sessions' closes through the existing bounded close scheduler.
- Drive it from the per-replica ring heartbeat, alongside the durable-ownership reconcile that already runs there, so the sweep happens on every replica regardless of traffic, leadership, or durable-row cleanup.
- No new selection logic and no new timing: eligibility, the idle TTL that protects a session freshly handed to a request, and the close path are unchanged.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `sticky-session-operations`: idle bridge-session eviction no longer depends on request traffic reaching the replica.
