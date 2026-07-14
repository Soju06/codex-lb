# Design: Purge stale bridge sessions on startup

## Context

Durable bridge sessions persist across restarts for multi-replica failover.
In single-instance operation, this persistence provides no failover target
and creates operational problems.

## Decision

Purge owned and stale ownerless rows on startup, before the first request
is served. The purge is fenced by `instance_id` - it only deletes rows
owned by this instance or ownerless rows with expired leases. Other
replicas' active rows are not affected.

## What is preserved

- `sticky_sessions` - lightweight account-affinity mappings, no stream
  leases, useful for prompt-cache continuity
- Other replicas' durable bridge rows - multi-instance safe
- `bridge_ring_members` - ring membership is managed separately

## What is removed

- `http_bridge_sessions` rows where `owner_instance_id == this_instance`
- `http_bridge_sessions` rows where `owner_instance_id IS NULL` and
  state IN (active, draining) and `lease_expires_at < now`
- Associated `http_bridge_session_aliases` for deleted rows

## Multi-instance safety

The purge is per-instance: it only deletes rows owned by the current
instance or ownerless stale rows. If replica A restarts, it purges its
own rows. Replica B's rows are untouched. If A crashed without graceful
shutdown, B may have already taken over (via `claim_session` with
`allow_takeover=True`), updating `owner_instance_id` to B - A's purge
won't touch them.

The only edge case: A crashes and restarts before B takes over. A purges
its own rows, and B cannot recover them. But A is back and can serve
requests directly, and the client sends `previous_response_id` itself.
