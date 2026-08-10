## Purpose and Scope

This change closes the gap between Codex's conversation-restart semantics and codex-lb's conservative legacy affinity. It applies only to a Codex goal continuation that resends enough state to start fresh on another account. The normative contract is in the delta for `sticky-session-operations`.

## Rationale and Constraints

Raw `codex_session` rows remain hard by default because their provenance is ambiguous during rolling upgrades: the key may be an old process-session identifier or an explicit account-scoped turn state. The goal-continuation marker provides intent, while the strict fresh-replay classifier proves that moving the request does not depend on stored upstream state. Both are required. Classification uses the canonical upstream body so accepted compatibility controls and transport-only envelope fields cannot make equivalent requests disagree.

Unavailable means a persisted account status of `PAUSED`, `RATE_LIMITED`, or `QUOTA_EXCEEDED`. A queue cap, retry exclusion, budget threshold, transient runtime-health decision, or healthy owner does not qualify. Retirement is compare-and-set so concurrent owner changes win.

Sticky rows are global, but an API key may authorize only a subset of accounts. Retirement authority follows the request's effective account-policy scope: a scoped request cannot tombstone a row owned by another pool, even when that owner is durably unavailable. Direct WebSocket account changes also discard proxy-generated turn-state from the retired account; only a turn-state header actually supplied by the client is preserved.

A successful guarded retirement is authoritative over account objects loaded before that transaction: the retired owner remains excluded for the rest of the selection attempt even if that snapshot still reports it active.

## Failure Modes

- A normal same-session request still returns the existing hard-affinity error while its owner is unavailable.
- A marked request with `previous_response_id`, nonblank `conversation`, an account-scoped file/image, unsafe payload controls, or unresolved tool output remains owner-bound.
- If the owner recovers or another request changes the row before retirement commits, the update does nothing and selection fails closed rather than discarding the newer state.
- If an authenticated request cannot select the persisted owner under its account policy, the request fails closed without mutating that global row.
- A pre-retirement selection snapshot can still contain the old owner. Successful retirement filters that owner before replacement selection so namespaced affinity cannot be recreated on it.

## Example

Session `thread-1` has a raw legacy mapping to account A. Account A becomes quota-exceeded, while account B is active. Codex sends the full conversation under `thread-1`, without a previous-response or conversation object, and includes `<codex_internal_context source="goal">Continue working toward the active thread goal.` The proxy tombstones the still-current raw A mapping, selects B, and records the namespaced process-session affinity to B. A later incremental turn resolves its response/session continuity to B.

## Operational Notes

No setting or migration is introduced. Existing affinity diagnostics and tombstone administration remain applicable. Rollback leaves any newly created tombstones readable by the prior code and does not affect the account data volume.
