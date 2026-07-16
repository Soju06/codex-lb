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

## Operational Notes

Operators can distinguish local account pressure through the stable `account_response_create_cap` and `account_stream_cap` reasons. The spillover behavior is zero-config because it mutates no ownership state; rollback restores conservative fail-closed selection without data conversion.

Related capability: `openspec/specs/sticky-session-operations/`.
