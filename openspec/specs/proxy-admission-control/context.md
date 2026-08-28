# Proxy Admission Control Context

## Purpose and Scope

This capability protects proxy work at global, traffic-class, transport, and account boundaries. It covers where admission decisions happen and how local capacity failures remain distinguishable from upstream rate limits.

See `openspec/specs/proxy-admission-control/spec.md` for normative requirements.

## Account-cap Spillover Decision

Bare process-session affinity is locality, not ownership. When its mapped account is at a response-create or stream cap, selection may use another eligible account for the current self-contained, pre-visible request. The mapping itself is left untouched, so later work returns to the original locality account when capacity recovers.

Persistent rebind was rejected because admission completes at different points in plain streaming, compact, direct WebSocket, and HTTP bridge flows. Moving the mapping would require settlement and compensating rollback across sticky rows, durable bridge rows, local registries, and shared sockets. Request-local spillover removes that distributed transaction.

## Constraints and Failure Modes

- Spillover ends at transport handoff. A late lease race returns the existing bounded local-cap error rather than switching a shared WebSocket or publishing a replacement bridge.
- Previous-response, file, conversation, turn-state, live/durable bridge, replay, and reattach ownership remain fail-closed.
- A single process must run per instance because account caps are partitioned across replicas, not safely across worker processes inside one instance.
- Repeated self-contained requests may use different alternates during sustained pressure; this is an accepted cache-locality trade-off.

## Example

Session `S` is mapped to account A. A has all response-create slots in use, while account B has capacity. A new self-contained request carrying only `S` may run on B, but the stored mapping still points to A. A later request that references a response created on B follows that response's hard owner index; it does not rely on `S`.

## Account Cap Sizing Across Replicas

Under the default `proxy_account_caps_scope = "partitioned"`, `proxy_account_stream_limit` (default 8) and `proxy_account_response_create_limit` are **cluster-wide targets**, not per-replica values. Each replica derives its own share of a positive cap locally from the sorted bridge-ring membership: `max(1, floor(cap / R) + 1 extra when its rank < cap mod R)` (`app/modules/proxy/cap_partitioning.py`). A cap of `0` stays unlimited everywhere. With the default stream cap of 8 and three replicas the shares are 3/3/2 — a single account can hold at most 2–3 concurrent streams per replica, which surprises operators who read the setting as per-replica. `proxy_account_caps_scope = "replica"` is the supported opt-out: every replica then enforces the full configured cap with no partitioning.

The effective caps are the **dashboard-persisted** values (`configured_account_concurrency_caps`); the environment settings only seed the initial dashboard row, and there is no dashboard path back to an unset state — so on an initialized deployment the cap is changed from the dashboard, and an env change alone never takes effect.

Sizing guidance:

- Choose a positive cap for the **cluster**: the total concurrent upstream streams one account may hold. Scaling replicas out does not raise it; it only re-partitions it.
- Every share is floored at one slot so an account never becomes unroutable on a replica; when `cap < replica_count` the cluster-wide aggregate therefore equals the replica count — and grows with each added replica — rather than honoring the configured cap.
- `proxy_account_stream_recovery_reserve` (default 1) is subtracted at **selection time only** — it keeps slots free for recovery/reconnect traffic and is deliberately not consulted when a warm session reacquires its lease between turns. On small per-replica shares the reserve is proportionally heavy: with a share of 2, selection sees 1 usable slot.
- Membership changes apply with hysteresis: a replica adopts a share **increase** only after the new partition has been stable for a window, while decreases apply immediately — so a missed heartbeat or rolling replacement cannot transiently inflate the aggregate toward upstream.
- Since turn-scoped leases (#1476), idle warm sessions do not occupy slots. A slot lost to an abnormal condition is reclaimed by the stale-lease sweep, whose stream threshold is NOT the raw `proxy_account_lease_ttl_seconds` (default 900s): a legitimately long-running stream must not be reclaimed mid-flight, so the effective bound is `max(lease TTL, longest stream/request budget) + 60s grace` (`_account_lease_stale_ttl_seconds`) — 7260s with the default 7200s Responses budgets. That is the true worst-case recovery time for a leaked stream slot.

Symptom of undersizing: persistent `account_stream_cap` errors and "Waiting for account capacity" retries while replicas are mostly idle. First response is raising `proxy_account_stream_limit` toward `desired-per-account-concurrency` (a common operating point is `~8 × replica_count`), not adding replicas.

## Operational Notes

Operators can distinguish local account pressure through the stable `account_response_create_cap` and `account_stream_cap` reasons. The spillover behavior is zero-config because it mutates no ownership state; rollback restores conservative fail-closed selection without data conversion.

Related capability: `openspec/specs/sticky-session-operations/`.
