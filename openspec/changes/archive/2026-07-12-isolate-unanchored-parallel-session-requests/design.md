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

The internal key hashes a server-generated request-scope nonce rather than the
client-controlled HTTP request ID. Distinct concurrent requests therefore
create distinct upstream websocket sessions even when a client repeats
`x-request-id`, while a retry inside one HTTP request keeps a stable lane. The
original bridge stays registered under the canonical session key and its model
metadata is not changed by the forked request.

The canonical session is reserved for that request at the first cancellable
handoff after lookup and before submission makes its queued activity visible.
There is no event-loop yield between a normal lookup return and that claim. A
different request that arrives after the claim forks instead of reusing the
apparently idle session. Submission clears the matching reservation in the same
synchronous section that increments the queued count, and an owned `finally`
also clears it on every pre-submit error or cancellation. Recovery paths claim
the same lease before awaiting API-key admission. Idle pruning and capacity
eviction both skip a session while this handoff lease is owned.

Unanchored owner forwarding uses protocol v2, whose main HMAC binds the
unanchored boolean in both true and false forms. The owner still receives the
generated downstream turn-state for response aliasing, but removes that value
from bridge lookup when the originating request was unanchored. The authenticated
boolean is carried through the owner request path even after a fork has changed
the current affinity kind, so the lookup-to-submit reservation still applies.
Empty and whitespace-only turn-state headers normalize to no anchor. A v2 origin
therefore cannot be downgraded by changing or stripping the boolean. A legacy
unanchored forward fails closed on a v2 owner, while a v2 signature fails
legacy validation on an old owner; mixed-version deployments never silently
fall back to the shared canonical response-create gate.

Explicitly anchored forwards retain the legacy-compatible primary HMAC (and
the existing client-IP companion HMAC) so ordinary continuity traffic keeps
working in both directions during a rolling upgrade. Only the newly introduced
unanchored state requires the incompatible v2 primary signature. Current owners
reject delimiter-bearing legacy signed fields because the old joined encoding
cannot authenticate their boundaries safely.

The v2 HMAC input uses a domain-separated canonical JSON object rather than the
legacy delimiter-joined field list. This prevents signed v2 fields from being
repacked into attacker-controlled affinity values to produce the same bytes
under legacy validation. The primary signature also binds whether client-IP
metadata exists; when present, the companion signature continues to bind the
actual address. Removing both headers therefore cannot silently erase owner-log
attribution.

Forked lanes use hard continuity strength. They are independent at creation,
but any durable turn-state or previous-response alias derived from that lane
must retain its account and owner binding on later requests. If a forwarded
request reaches the canonical owner and collides there, that owner keeps the
new fork local and durably claims it instead of rendezvous-hashing a second hop.

## Capacity and observability

Forked lanes use the existing bridge-capacity, account-selection, lifecycle,
and durable-registration paths.  If no lane capacity exists, the existing
bounded local overload behavior applies.  Each fork emits an
`unanchored_parallel_fork` diagnostic with a low-cardinality reason.
