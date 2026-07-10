# Design

## Continuity boundary

A process-level session header is useful for sequential locality, but it does
not prove that two concurrent first-turn requests share response state.  The
bridge may split a request only when it carries neither `previous_response_id`
nor a turn-state header.  Those explicit anchors keep the existing owner-bound
behavior.

## Fork decision

An unanchored `session_header` request receives an
`internal_unanchored_parallel` key when any of these is true:

- creation of the canonical session bridge is still in flight;
- the canonical session has a visible queued or active request; or
- the canonical idle session records a different model class.

The internal key hashes the inbound HTTP request ID and uses soft strength.
Distinct concurrent requests therefore create distinct upstream websocket
sessions, while a retry inside one HTTP request keeps a stable lane. The
original bridge stays registered under the canonical session key and its model
metadata is not changed by the forked request.

## Capacity and observability

Forked lanes use the existing bridge-capacity, account-selection, lifecycle,
and durable-registration paths.  If no lane capacity exists, the existing
bounded local overload behavior applies.  Each fork emits an
`unanchored_parallel_fork` diagnostic with a low-cardinality reason.
