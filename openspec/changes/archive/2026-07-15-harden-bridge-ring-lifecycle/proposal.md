# Harden bridge ring lifecycle

## Why

Multi-replica bridge ownership is fenced only on the alias path: `renew_session` and `release_session` in `DurableBridgeRepository` are unfenced check-then-write, so a fenced-out old owner can overwrite the new owner's lease, state, and `latest_turn_state` continuity anchor (lost update on PostgreSQL READ COMMITTED and cross-process SQLite). A replica that loses ownership is never told: it keeps an orphaned upstream websocket and account lease for up to the 900s idle TTL, causing spurious per-account stream-cap 429s after ring changes. Crashed-owner durable rows (ACTIVE with expired lease) and abandoned DRAINING rows are never purged, and `bridge_ring_members` rows accumulate forever. During the deliberate 15-30s post-shutdown ring grace, turn-state-anchored requests forwarded to the dead owner return client-visible 503s even though the durable lease was already released. None of this lifecycle is covered by any main spec.

## What Changes

- Rewrite `DurableBridgeRepository.renew_session` and `release_session` as single fenced `UPDATE ... WHERE id AND owner_instance_id AND owner_epoch ... RETURNING` statements, mirroring `register_owned_alias`; fenced-out callers mutate nothing and receive the current owner snapshot.
- Losing-replica eviction: a fenced-out renewal or alias write eagerly closes the local in-memory session (closing the upstream websocket and releasing the account lease) instead of silently adopting the new epoch; a reconciliation sweep piggybacked on the 10s ring-heartbeat task batch-checks durable ownership for local sessions past the lease TTL and closes fenced-out ones.
- Orphan purge: new `purge_abandoned_before(cutoff)` deletes ACTIVE/DRAINING `http_bridge_sessions` rows whose lease is expired and whose `last_seen_at` is older than the retention cutoff (aliases in the same batch), invoked by the existing sticky-session cleanup scheduler; the same pass deletes `bridge_ring_members` rows whose heartbeat is older than 24h.
- Post-shutdown grace 503 fix: on a `bridge_owner_unreachable` forward failure, allow local takeover for turn-state-anchored requests when a fresh durable lookup shows no active lease (released, expired, or DRAINING); live-lease owners still fail closed with the retryable 503.
- New `bridge-ring-membership` capability spec codifying the currently spec-less ring lifecycle (registration before serving bridge traffic, heartbeat cadence, stale threshold, shutdown stale-aging grace, stale-row purge), plus new fencing/eviction/purge requirements in `sticky-session-operations` and one added scenario in `responses-api-compat`.
- No Alembic migration: all changes use existing tables and columns.

## Non-goals

- DB-clock arbitration for ring staleness and lease expiry (follow-up `guard-bridge-ring-topology`).
- Advertise-URL guardrails, readiness `require_endpoint` alignment, and boot-nonce duplicate-instance-id detection (follow-up `guard-bridge-ring-topology`).
- Forward-signature timestamp/nonce replay protection and firewall scoping of `/internal/bridge/*` (follow-up `harden-bridge-forward-replay`).
