## Purpose and Scope

This change closes the gap between Codex's conversation-restart semantics and codex-lb's conservative legacy affinity. It applies only to a Codex goal continuation that resends enough state to start fresh on another account. The normative contract is in the delta for `sticky-session-operations`.

## Rationale and Constraints

Raw `codex_session` rows remain hard by default because their provenance is ambiguous during rolling upgrades: the key may be an old process-session identifier or an explicit account-scoped turn state. The goal-continuation marker provides intent, while the strict fresh-replay classifier proves that moving the request does not depend on stored upstream state. Both are required. Classification uses the canonical upstream body so accepted compatibility controls and transport-only envelope fields cannot make equivalent requests disagree. Because the incoming header cannot prove which source wrote an old raw row, restart abandonment is persisted with `session_header` scope; an explicit turn-state lookup of equal text still receives the retained owner. The scoped marker leaves the historical timestamp tombstone empty so an older replica that ignores scope continues to treat the retained owner as hard.

Unavailable means a persisted account status of `PAUSED`, `RATE_LIMITED`, or `QUOTA_EXCEEDED`. A queue cap, retry exclusion, budget threshold, transient runtime-health decision, or healthy owner does not qualify. Retirement is compare-and-set so concurrent owner changes win.

Sticky rows are global, but an API key may authorize only a subset of accounts. Retirement authority follows account assignment and security authorization before model/service-tier eligibility: a scoped request cannot mark a row owned by another pool, while an in-scope owner does not lose mutation authority merely because it cannot serve the replacement model. Direct WebSocket account changes also discard proxy-generated turn-state from the retired account; only a turn-state header actually supplied by the client is preserved.

Goal-restart retirement occurs only inside account selection. A live or durable HTTP bridge for the same process session is not additional ownership evidence after the client proves a self-contained resend, so bridge reuse, preferred-owner promotion, and remote forwarding must not consume the request before selection. A successful guarded retirement is also authoritative over any account objects loaded before that transaction; the retired owner remains excluded for the rest of the selection attempt even if that snapshot still reports it active. A selector that loses the retirement compare-and-set to another selector's scoped marker carries the marker's retained owner into the same exclusion path.

## Failure Modes

- A normal same-session request still returns the existing hard-affinity error while its owner is unavailable.
- A marked request with `previous_response_id`, nonblank `conversation`, an account-scoped file/image, unsafe payload controls, or unresolved tool output remains owner-bound.
- If the owner recovers or another request changes the row before retirement commits, the update does nothing and selection fails closed rather than discarding the newer state.
- If an authenticated request cannot select the persisted owner under its account policy, the request fails closed without mutating that global row.
- If a process-session ID collides with an explicit turn-state value, restart recovery moves only process-session interpretation; the turn-state request remains bound to the retained owner.
- If the unavailable owner is in policy scope but cannot serve the requested model, guarded abandonment remains authorized and model filtering applies only to replacement selection.
- A live HTTP bridge can retain a detached ACTIVE account object after its persisted owner becomes unavailable. A verified restart bypasses and retires that bridge instead of trusting the stale object.
- A pre-retirement selection snapshot can still contain the old owner. Successful retirement, or an authoritative reread after losing the retirement compare-and-set, filters that owner before replacement selection so namespaced affinity cannot be recreated on it.
- An older replica does not understand source scope. The scoped marker therefore leaves the historical timestamp tombstone empty so that replica keeps the retained hard owner instead of globally abandoning it.

## Example

Session `thread-1` has a raw legacy mapping to account A. Account A becomes quota-exceeded, while account B is active. Codex sends the full conversation under `thread-1`, without a previous-response or conversation object, and includes `<codex_internal_context source="goal">Continue working toward the active thread goal.` The proxy marks the still-current raw A mapping abandoned for `session_header`, selects B, and records namespaced process-session affinity to B. A later session turn resolves to B; an explicit `x-codex-turn-state: thread-1` request still resolves the retained raw owner A and fails closed while A is unavailable.

## Operational Notes

No setting is introduced. The nullable abandonment-scope migration requires no historical backfill because a non-null timestamp with NULL scope continues to mean global abandonment. Source-qualified markers keep that timestamp NULL, so older binaries safely retain hard ownership during rollout or rollback. Dropping the scope column discards only the restart-recovery marker and restores conservative hard ownership. Existing affinity diagnostics and tombstone administration remain applicable.
