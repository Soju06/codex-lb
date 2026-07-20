# Design

## Why not a new durable lookup at verification time

The natural first instinct is to give `_verified_cross_transport_fresh_replay`
its own durable-storage fallback: check the in-memory cache, and on a miss,
query `request_logs` directly. That duplicates a query the caller already
just made: `_resolve_websocket_previous_response_owner` unconditionally reads
the same `request_logs` row (for the same `previous_response_id`) to resolve
the owner account, on every request that carries a `previous_response_id` —
not just the failure path. Adding a second, independent lookup there would
double a per-request database read for every continuation, for a benefit that
lookup already produces as a side effect.

Instead, the owner-resolution helper warms the *existing* in-memory
continuity cache from the row it already fetched. The purely-in-memory
verification function (`_verified_cross_transport_fresh_replay`) is otherwise
unchanged; callers that want durable-backed recovery simply re-invoke it after
owner resolution has had a chance to warm the cache. This keeps the one
security-sensitive verification primitive exactly as it was (audited,
tested), and only changes where its input data can come from.

## Never clobber a live entry

The warm step only writes when the cache key is *absent*. A durable
row is necessarily stale relative to anything this process observed directly
(the whole reason durable fallback is needed is that this process didn't see
the completion). If a live, more-current entry exists for the same session,
overwriting it with an older durable row would replace correct data with
worse data for no benefit — so the warm step is skip-if-present, never
replace.

## Direct retry path: recompute, don't relocate

`_stream_with_retry` computes `verified_fresh_replay_payload` once, upfront,
before owner resolution runs. Every later recovery branch reads that same
variable by closure/name, not by re-deriving it — so the minimal, lowest-risk
change is to recompute it in place, immediately after owner resolution, only
when it is still `None`. Every existing recovery branch downstream
automatically observes the recomputed value; nothing else about the retry
state machine changes.

## HTTP bridge: a parallel branch, not a shared gate

The bridge already has a fresh-replay recovery branch, but it is scoped
tightly to the proxy-injected reattach case (client sent no
`previous_response_id`; the proxy silently attached one to reuse a session)
and gated on `durable_full_resend_anchor_count`/`fingerprint`, which come from
`HttpBridgeSessionRecord`, a different durable store than `request_logs`. That
existing gate has correct, narrow semantics for its scenario and must not be
loosened. The client-anchored case (client supplied `previous_response_id`
directly — the scenario this change fixes) gets its own branch, checked
first, using the same `_verified_cross_transport_fresh_replay` primitive the
direct retry path uses. The two branches never overlap:
`proxy_injected_previous_response_id` is only ever true in the first scenario
and only ever false in the second, by construction.

## Replay boundary (unchanged invariant)

Exactly as before: a fresh replay is only ever permitted when the resend body
is self-contained (no dangling tool-call references, no file uploads — those
have their own hard ownership) and the durable or in-memory fingerprint proves
an exact prefix match against what the owner actually completed. Anything
short of that still fails closed with `previous_response_owner_unavailable`,
identically to today.
