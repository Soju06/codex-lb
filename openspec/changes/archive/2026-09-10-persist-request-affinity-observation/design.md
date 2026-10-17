## Context

See proposal.md for the accepted #2349 requirement. Direct streams already emit some attempt failures separately; bridge and native WebSocket finalization use per-request state. The shared bridge reader can finalize several turns, so ambient task context cannot identify a turn safely.

## Goals / Non-Goals

Carry immutable content-free metadata explicitly through existing log writes. Preserve the existing row granularity and all asynchronous ownership. No attempt-history schema, affinity derivation changes, frontend, or new settings.

## Decisions

Capture a small immutable observation from the existing source classification and affinity policy. Carry it in per-request state and explicit parameters. Hash the resolved policy selection key, not the payload prompt-cache field, since those can differ. A cleared key has no hash. Refresh the policy snapshot where existing routing code replaces the policy, preserving the existing source classification.

Pass the observation through the existing detached log writer and repository. Expose nullable scalar fields to administrators through the current API; the existing sensitive-metadata gate hides them from guests. Do not introduce a ContextVar: reused bridge reader tasks and failure fan-out do not have per-turn context.

The three fields describe the row's existing attempt or final request state. For example, a bridge final row can cover a retry; its metadata describes the final policy and is not evidence of every intermediate send. Separate attempt rows retain their own snapshot.

Use 16 hexadecimal SHA-256 characters as requested. This enables equality grouping, not anonymization against guessed low-entropy keys. Existing request-log access and retention apply. No new index is needed for initial SQL inspection.

## Risks / Trade-offs

- Missed failure emitter: inventory every writer and cover public failure/settlement paths.
- Cross-turn metadata leakage: immutable values on each request state, no shared ambient carrier.
- Concurrent schema changes: unique revision on fetched main's single head, with populated upgrade/downgrade proof and a pre-publication upstream recheck.

## Migration Plan

Add nullable columns after `20260910_010000_dashboard_spool_retention`. Preserve this published revision and its parent. Main `d6a7ca662860e2b427e462f434319273bcd240bd` added a sibling guest-session migration during review; the public populated upgrade reproduced the resulting two-head failure. Append the no-op `20260910_180000_merge_affinity_guest_heads` revision joining affinity and guest-session histories. Both populated parent histories must upgrade without losing data; merge-only downgrade and reupgrade must leave schema and data intact. Historical affinity values remain null. Downgrading the affinity branch removes only its columns. Live migration is outside this task; hosted delivery goes to readiness after verification.
