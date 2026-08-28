# Context

The bridge opens or reuses a session before the request-specific pre-created
retry decision. That ordering is required for continuity and account routing,
but it means a cooldown decision can arrive after a WebSocket has already been
registered. Returning an error alone is insufficient: the session remains in
the registry and may be selected by a later request.

The existing `_retire_http_bridge_after_drain_if_ready` helper is the correct
lifecycle owner. Setting both control flags makes `_http_bridge_session_reusable_for_request`
reject new work immediately. The helper closes only when there is no visible
pending request, queue count, unanchored reservation, or competing close, and
otherwise leaves the session marked for retirement until the remaining owner
settles it.

The startup terminal path is equivalent to late suppression from a lifecycle
perspective: it has an already-created session but no upstream `response.create`
dispatch. It must use the same flags and helper so both paths are idempotent
and share registry detachment, alias cleanup, lease settlement, and bounded
socket close behavior.

Replay exceptions remain intentionally unchanged. A proof-gated full resend or
an operation-fenced continuity replay is an explicitly authorized dispatch;
those paths must not be retired by the generic cooldown suppression branch.
