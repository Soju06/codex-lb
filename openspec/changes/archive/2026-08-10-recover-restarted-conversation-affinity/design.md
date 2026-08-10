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
- Add a setting, schema migration, or new client-visible error code.

## Decisions

### Derive a typed restart capability during affinity classification

The Responses request classifier will expose whether any input item carries the already-recognized `<codex_internal_context source="goal">` prefix. `_sticky_key_for_responses_request` will grant an `abandon_unavailable_legacy_owner` capability only when that marker is present and the complete serialized request passes `responses_payload_is_account_neutral_fresh_replay`.

This reuses the proof applied to cross-account replay instead of maintaining a second list of unsafe fields. Inferring restart from a missing `previous_response_id` alone was rejected because ordinary first turns and lossy incremental requests have that shape. Introducing a new client header was rejected because the deployed Codex client already supplies a stable payload marker.

### Limit retirement to legacy process-session ownership and durable statuses

Selection may consume the capability only for a raw compatibility row reached through typed `session_header` provenance. Explicit turn-state rows remain hard. The repository update also requires the current owner to be `PAUSED`, `RATE_LIMITED`, or `QUOTA_EXCEEDED`; local capacity, runtime-health, exclusion, and budget decisions cannot authorize retirement.

### Tombstone with compare-and-set, then rerun normal selection

The repository will atomically set `continuity_abandoned_at` only when the key, kind, expected account, non-tombstoned state, and unavailable account status still match. Selection clears its cached legacy owner and repeats its normal loop. The existing tombstone semantics then authorize fresh selection and allow the namespaced process-session row to claim the replacement owner.

Deleting the row outright was rejected because tombstones distinguish deliberate continuity abandonment from an unknown owner. Blindly updating after an earlier status read was rejected because a concurrent rebind or account recovery could otherwise be lost.

## Risks / Trade-offs

- [A forged marker requests owner abandonment] → The complete payload must still be account-neutral and self-contained, the caller controls only its own session key, and retirement occurs only while the persisted owner is unavailable.
- [A concurrent request changes ownership or restores the account] → One compare-and-set statement verifies both mapping owner and account status at write time; a miss preserves fail-closed behavior.
- [A restart includes unresolved tool output or account-scoped content] → The existing fresh-replay classifier denies the capability, leaving the hard row untouched.
- [Transport wiring drifts] → Carry one typed affinity-policy flag through the shared selection boundary and explicitly forward it from the direct WebSocket call site that expands policy fields.

## Migration Plan

No database migration is required because `continuity_abandoned_at` already exists. Deploy the new image, then reproduce a marked restart against an unavailable legacy owner and verify a replacement account is selected. Rollback is an image replacement; existing tombstones remain compatible with the prior stale-owner cleanup behavior.

## Open Questions

None.
