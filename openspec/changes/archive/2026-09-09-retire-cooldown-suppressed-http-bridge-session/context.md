# Context

The bridge opens or reuses a session before the request-specific pre-created
retry decision. That ordering is required for continuity and account routing,
but it means a cooldown decision can arrive after a WebSocket has already been
registered. Returning an error alone is insufficient: the session remains in
the registry and may be selected by a later request.

The existing `_retire_http_bridge_after_drain_if_ready` helper is the correct
lifecycle owner. Setting both control flags makes `_http_bridge_session_reusable_for_request`
reject new work immediately. The flags are set only when the session is
unowned under `pending_lock` (no visible pending request, queue count,
admission waiter, foreign unanchored reservation, or competing close); an
owned session is left unmarked because the pre-dispatch fence fails any
non-owner submit on a marked session, and the one submit a cooldown admits —
the half-open probe — is exactly the turn a concurrent suppression would
otherwise fence off.

That probe is visible only if admission ownership starts before the dispatch
registration: the submit counts itself as an admission waiter at entry, the
dispatch registration takes the count over, and every pre-dispatch exit
(interruption cleanup, retiring fence, submit finalizer) releases it and
re-runs the retirement it deferred.

The startup terminal path is equivalent to late suppression from a lifecycle
perspective: it has an already-created session but no upstream `response.create`
dispatch. It must use the same flags and helper so both paths are idempotent
and share registry detachment, alias cleanup, lease settlement, and bounded
socket close behavior.

Replay exceptions remain intentionally unchanged. A proof-gated full resend or
an operation-fenced continuity replay is an explicitly authorized dispatch;
those paths must not be retired by the generic cooldown suppression branch.
