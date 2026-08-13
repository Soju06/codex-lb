## Context

Current replicas distinguish newly namespaced soft process-session affinity from raw legacy `codex_session` rows. A raw row always wins because it may represent hard turn-state continuity. Codex conversation restart, however, reuses the process-session identifier while deliberately resending the thread without `previous_response_id`; the request body includes the existing goal-continuation internal context marker. If the raw owner is quota-unavailable, selection currently treats the row as hard and returns `hard_affinity_saturated` forever (or until the six-hour stale-owner cleanup), even though this restart payload no longer needs the owner's upstream state.

## Goals / Non-Goals

**Goals:**

- Recover an explicitly marked, self-contained Codex restart immediately when its legacy owner is durably unavailable.
- Reuse the existing strict fresh-replay classifier for account-neutrality and tool-state safety.
- Preserve hard ownership for every ordinary or account-dependent request.
- Make owner retirement safe under concurrent selection and rebinding.

**Non-Goals:**

- Reallocate hard rows because of local caps, retry exclusions, transient health, or budget pressure.
- Make arbitrary full-resend requests mobile without the Codex restart marker.
- Change previous-response, conversation, file, bridge, or turn-state ownership semantics.
- Add a setting or new client-visible error code.

## Decisions

### Derive a typed restart capability during affinity classification

The Responses request classifier will expose whether any input item carries the already-recognized `<codex_internal_context source="goal">` prefix. `_sticky_key_for_responses_request` will grant an `abandon_unavailable_legacy_owner` capability only when that marker is present and the canonical upstream request body passes `responses_payload_is_account_neutral_fresh_replay`.

This reuses the proof applied to cross-account replay instead of maintaining a second list of unsafe fields. Canonical request serialization is required because raw model dumps retain accepted compatibility controls and the direct-WebSocket response-create discriminator even though neither is upstream account state. Inferring restart from a missing `previous_response_id` alone was rejected because ordinary first turns and lossy incremental requests have that shape. Introducing a new client header was rejected because the deployed Codex client already supplies a stable payload marker.

### Limit retirement to legacy process-session ownership and durable statuses

Selection may consume the capability only for a raw compatibility row reached through typed `session_header` provenance. That provenance describes the current request, not the historical row: raw storage mixed process-session and explicit turn-state values, so equal client-controlled text cannot prove which source wrote it. The repository therefore marks abandonment as applying only to `session_header` interpretation and retains the row's account for explicit `turn_state` lookup. The repository update also requires the current owner to be `PAUSED`, `RATE_LIMITED`, or `QUOTA_EXCEEDED`; local capacity, runtime-health, exclusion, and budget decisions cannot authorize retirement.

Mutation authority is the authenticated account-assignment and security-policy scope before requested-model and service-tier filtering. Model eligibility still constrains the replacement pool, but cannot make an otherwise authorized raw owner appear out of scope.

### Mark source-qualified abandonment with compare-and-set, then rerun normal selection

The repository will atomically set `continuity_abandonment_scope=session_header` while leaving `continuity_abandoned_at` NULL, only when the key, kind, expected account, unmarked state, and unavailable account status still match. Selection clears its cached legacy owner and repeats its normal loop. Source-aware lookup treats that row as abandoned for process-session selection but continues returning the retained account to an explicit turn-state request. The scope-only representation is intentionally asymmetric with historical global tombstones: an older binary ignores the unknown scope but sees no timestamp tombstone, so it continues to fail closed on the retained owner during rollout or rollback.

Deleting the row outright was rejected because tombstones distinguish deliberate continuity abandonment from an unknown owner. Blindly updating after an earlier status read was rejected because a concurrent rebind or account recovery could otherwise be lost.

The normal loop may still hold account objects loaded before retirement. Successful retirement therefore records the retired account for the lifetime of the selection call and filters it from later iterations. A compare-and-set loser rereads the marker and receives the retained retired account separately from ownerless affinity, then applies the same exclusion. Re-reading every account after the write was rejected because the request needs only one authoritative exclusion and the broader refresh would add unrelated database work.

### Force verified HTTP restarts through account selection

HTTP bridge reuse normally returns before account selection, and durable bridge metadata can promote the prior account to a required preferred owner or forward the request to another replica. For a verified self-contained goal restart, these paths would prevent the guarded raw-owner retirement from running. The bridge path therefore ignores that prior bridge ownership for this request, detaches any matching local bridge, and creates a replacement only after ordinary selection has evaluated the retirement capability. A bridge with visible work is detached without interrupting that work; an idle bridge is closed normally.

Detachment does not end lifecycle ownership. A predecessor request that reserved its lane before replacement keeps request-scoped submit authority after queue publication clears the mutable reservation field. Detached live generations remain tracked separately from the canonical key, count against capacity until close finishes, participate in drain status, and are included in shutdown cleanup. Treating the canonical map as the complete live-generation registry was rejected because repeated restarts could otherwise hide unbounded sockets, readers, durable leases, and account leases.

The admission-only `closed` flag does not make pending or queued settlement disappear from drain status. Conversely, an idle predecessor is immediately finalizable: when it alone fills the configured cap, the authorizing restart owns its bounded close synchronously and rechecks actual lifecycle capacity before opening the replacement. Scheduling that close in the background was rejected because cap enforcement could run first and fail the same recovery request even though it had already detached the only idle generation.

The predecessor may drain its admitted response but cannot publish newly learned aliases under a key now owned by the replacement. Detached ownership is removed only by common close finalization so direct terminal-error paths and bounded background closes share the same capacity lifecycle. Request-final cleanup rechecks detached generations after reservations are released, and account-level invalidation closes both canonical and detached sockets.

Close finalization shields its resource task from caller cancellation and re-raises cancellation only after releasing the reader, socket, durable lease, and account lease. A newly created local generation also advances the durable owner epoch whenever the existing durable row still names the same replica identity. Replica identity is a routing destination, not a websocket-generation fence; without the epoch advance, a delayed release from the predecessor (or a prior process using the same configured identity) could close the replacement's lease.

## Risks / Trade-offs

- [A forged marker requests owner abandonment] → The complete payload must still be account-neutral and self-contained, retirement occurs only while the persisted owner is unavailable, and source-qualified abandonment cannot erase a colliding explicit turn-state owner.
- [A concurrent request changes ownership or restores the account] → One compare-and-set statement verifies both mapping owner and account status at write time; a miss rereads authoritative state and preserves fail-closed behavior.
- [Another restart wins the retirement compare-and-set] → The reread returns the retained retired owner as exclusion evidence so stale account inputs cannot re-pin it.
- [A restart includes unresolved tool output or account-scoped content] → The existing fresh-replay classifier denies the capability, leaving the hard row untouched.
- [Transport wiring drifts] → Carry one typed affinity-policy flag through the shared selection boundary and explicitly forward it from the direct WebSocket call site that expands policy fields.
- [A stale bridge or account snapshot restores the old owner] → Verified HTTP restarts bypass bridge reuse/forwarding, and successful retirement or a winner's scoped marker excludes the old owner from all later iterations of the current selection call.

## Migration Plan

Add nullable `sticky_sessions.continuity_abandonment_scope`. Existing rows require no backfill: a non-null abandonment timestamp with NULL scope retains its historical meaning as a global stale-hard tombstone. Goal restart writes `session_header` with a NULL timestamp; ordinary upsert clears both fields, and stale-hard cleanup may later promote a source-qualified marker to global after the normal age threshold. Older binaries and a downgraded schema see the retained account as hard ownership because the timestamp remains NULL. Downgrade therefore loses only the source-qualified recovery marker rather than weakening ownership.

## Open Questions

None.
