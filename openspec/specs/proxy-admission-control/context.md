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

## Bridge Reader Scheduling: Persistent Wakeup Waiter and Fast-Acquire Session Locks

The HTTP bridge upstream reader (`_relay_http_bridge_upstream_messages`) waits on two things per iteration: the upstream receive and the session's `upstream_reader_wakeup` event, which `request_submit` sets after a send so the reader re-evaluates its receive deadline. Two scheduling-only decisions (2026-09-03 CPU campaign; no payload, ordering, or lock-semantics change):

- **The wakeup waiter is reused across iterations.** The reader used to spawn a fresh `upstream_reader_wakeup.wait()` task for every upstream message and cancel it through the long-lived-child cleanup helper (`sleep(0)` + `cancel()` + timed `asyncio.wait`) as soon as the message arrived — about six event-loop trips of pure overhead per relayed delta (~1.0–1.5% of GIL samples in the production profile; 65.7 → 10.3 µs/message under uvloop in the pattern micro-benchmark). `asyncio.Event.wait()` waiters are level-triggered: one registered before the loop-top `clear()` still fires on the next `set()`, so an un-fired waiter is kept and only re-created after it fires. The `clear()` deliberately stays before the deadline snapshots (both snapshot helpers may yield; a `set()` landing during the snapshot must wake the wait, not be erased). A `set()` that lands while a message is being processed completes the waiter; the loop consumes it without awaiting at the top of the next iteration because that send is already represented in the snapshot. The reader's `finally` cancels the long-lived waiter once at loop exit.
- **Per-session bridge locks use `fast_lock()`** (`app/core/utils/locks.py`, `anyio.Lock(fast_acquire=True)`): `pending_lock`, `lifecycle_lock`, `prewarm_lock`, `recovery_alias_lock`, and the single-request failure lock in `request_submit`. anyio's default `Lock` runs a cancel-shielded `sleep(0)` checkpoint after every uncontended acquire; the reader acquires `pending_lock` three times per delta event for microseconds of synchronous bookkeeping and already yields on network I/O every iteration, so the checkpoint only added a scheduler round trip per acquire (3.96 → 0.76 µs under uvloop). Contended acquires still suspend, `checkpoint_if_cancelled` still runs, and `acquire_nowait`/`WouldBlock` are unchanged. The WebSocket-relay locks keep the default constructor for now: several tests there observe side effects at the post-acquire checkpoint, so that switch needs its own change.

Observable deltas operators may notice:

1. A cancellation that is already pending when a task reaches an uncontended bridge-lock acquire is now delivered at the task's next `await` instead of at the post-acquire checkpoint. Bodies under `pending_lock` are synchronous bookkeeping (see the "pending_lock critical sections do not suspend" requirement), so the cancellation lands at the same logical boundary — after the critical section — rather than inside it.
2. `_http_bridge_pending_count_nowait` hits `WouldBlock` less often because the lock is no longer held across an extra loop turn per acquire. Expect fewer `http_bridge_pending_count_unavailable context=idle_prune` warnings (hundreds per hour in production before the change) and more sessions actually evaluated by idle prune on each sweep; eviction cadence therefore converges toward the configured idle TTL rather than being skipped for busy sessions.
