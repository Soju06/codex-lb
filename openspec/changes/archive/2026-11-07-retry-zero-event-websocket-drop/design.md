## Context

See proposal.md for the reported incident. The bridge already owns reconnect, account leases, create gates, continuity proofs and a request replay counter. Its older transport-close path also allowed an extra clean-close retry and accepted-lifecycle replay.

## Goals / Non-Goals

Use that existing owner to recover an eventless socket loss. Preserve direct WebSocket and explicit capacity-error behavior. No settings, dependencies, migrations or deployment are needed.

## Decisions

- Pass a zero-event mode into precreated retry rather than building a second request dispatcher. Require one pending request, no events/output, no prior replay and no previous_response_id; a dead socket alone does not prove an anchor portable.
- Refuse durable operations outside submitted state, and retain operation identity on the original owner. Recheck pending ownership after reconnect so cancellation cannot create a new send.
- Prefer the original account without the older close-1011 exclusion. Retain mandatory account ownership and existing selector fallback for portable requests. When selection cannot proceed, return through the existing failure path without recovery sleeps.
- Normalize typed receive transport exceptions into the same terminal-message path. Ordinary processing exceptions retain their failure behavior.
- Log the send outcome as zero_event_replay with detail=resent/refused/failed/cancelled. Resent describes dispatch, not eventual completion.

## Risks / Trade-offs

- A silent upstream can have accepted work without emitting a response event. The requested recovery policy permits one retry at this boundary; positive events, buffered output, durable acknowledgement and an existing replay budget still fail closed.
- Continuations that depend on socket-local anchors remain unrecoverable through transport replay. Existing explicit stale-anchor proof paths retain ownership of that recovery.
- Reconnect can fail; the original error/selection failure remains client-visible and no further transport send is attempted.
