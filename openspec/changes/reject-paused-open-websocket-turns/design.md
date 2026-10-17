## Context

An open direct WebSocket retains an account object while account availability changes through the dashboard and the existing cluster invalidation bus. Selection-time eligibility cannot protect later frames.

## Goals / Non-Goals

Apply the existing unavailable-account policy at each response-create dispatch, while preserving the shared socket and already dispatched responses. No new routing policy, settings, schema, reconnect retention or transport changes.

## Decisions

Use the existing synchronous routing-availability cache immediately after admission waits and before binding the dispatch owner. A stale ORM status is not authoritative. A database query per frame would add latency and duplicate the existing invalidation mechanism.

Reject only the unsent request through existing reservation/gate cleanup and terminal error handling. Record its failure without marking the account unhealthy or closing its transport. A continuation receives `previous_response_owner_unavailable`; a fresh request receives `preferred_account_unavailable`.

## Risks / Trade-offs

- Cross-replica changes become visible after the existing invalidation bus converges; this change adds no stronger consistency guarantee.
- Shared resources must remain owned by the socket while per-request reservations are released. Route-level tests cover an active response, stale account objects, late pause during admission and reactivation.

## Migration Plan

No migration. Normal code rollback restores prior behavior.

## Open Questions

None.
