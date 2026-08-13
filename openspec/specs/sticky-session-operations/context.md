# Sticky Session Operations Context

## Purpose and Scope

This capability covers operational control and routing semantics for sticky-session mappings. It distinguishes hard account/session ownership from bounded cache locality, including bare process-session spillover under account caps.

See `openspec/specs/sticky-session-operations/spec.md` for normative requirements.

## Decisions

- Sticky-session rows store an explicit `kind` so prompt-cache cleanup can target only bounded mappings.
- Bare process-session headers use a header-inaccessible, source-separated storage key and are soft only for self-contained pre-visible work.
- Account-cap spillover is request-local: it selects an alternate without deleting or rebinding the process-session row.
- Raw and legacy Codex rows remain hard during rolling upgrades because they may represent explicit turn-state ownership.
- A raw legacy Codex owner can be abandoned only for an explicit goal-continuation restart whose canonical upstream payload passes the account-neutral fresh-replay proof, and only while that owner has a persisted unavailable status. Canonicalization keeps accepted compatibility fields and transport envelopes from changing classification. The compare-and-set marker is scoped to `session_header`, so an explicit turn-state lookup with colliding raw text retains the stored owner; a concurrent rebind or owner recovery still wins. The scoped marker deliberately leaves the historical global-tombstone timestamp empty, so replicas that do not understand scope continue to fail closed on the retained owner.
- Restart mutation authority is the authenticated account-assignment and security-policy scope before model and service-tier eligibility. Model filtering constrains only replacement selection.
- The retired owner is excluded from stale account snapshots for the remainder of the request, including when another selector wrote the scoped marker and this selector discovers it after losing the compare-and-set.
- Durable file pins, responses, conversations, live/durable bridges, replay, and reattach sources are independent hard evidence; conflicting evidence fails closed instead of using source precedence. Opaque file IDs with no live durable pin remain unpinned for compatibility with uploads that occurred outside the current process.
- Dashboard prompt-cache TTL is persisted in settings so operators can adjust it without restart.
- Background cleanup removes stale prompt-cache rows proactively, while manual delete and purge endpoints provide operator override.

## Constraints

- Historical sticky-session rows created before the `kind` column are backfilled conservatively to a durable kind to avoid accidental purge.
- Durable `codex_session` and `sticky_thread` mappings are never deleted by automatic cleanup.
- HTTP forbids CR/LF in headers and affinity parsing strips surrounding whitespace, while database text preserves LF. The internal soft-key sentinel therefore cannot be reproduced by a normalized client turn-state header.
- Every transport resolves live and durable turn-state aliases; an existing route or socket is not itself proof that a newly supplied conversation belongs to that account.
- File owner pins live in the shared application database. Cross-replica bridge forwarding still authenticates the origin-resolved owner, but the receiving replica must revalidate that owner against a fresh durable lookup.

## Failure Modes

- Cleanup failures are logged and retried on the next interval; request handling continues.
- Manual purge and delete operations are dashboard-auth protected and return normal dashboard API errors on invalid input or missing keys.
- Mixed-version replicas may temporarily produce both raw and namespaced rows. The raw row wins conservatively, which may reduce spillover but cannot weaken continuity.
- A raw process-session value may collide with an explicit turn-state value. Source-qualified abandonment lets the process session recover without making the retained raw account disappear from turn-state lookup.
- Partial file-pin coverage or conflicting hard-owner metadata returns a stable fail-closed error before upstream dispatch; zero file-pin coverage preserves the established opaque-ID forwarding path.
- A turn-state token learned from a retired WebSocket is discarded before a movable bare-session request connects to another account.
- An ordinary same-session request, or a goal-marked request that still carries previous-response, conversation, account-scoped file/image, or unresolved tool state, remains fail-closed on an unavailable raw owner.
- Local caps, retry exclusions, transient runtime health, and budget pressure never authorize legacy hard-owner abandonment.
- Selection inputs can predate the guarded retirement transaction. Once retirement succeeds, or once a compare-and-set loser rereads the winner's scoped marker, the old owner is filtered from those inputs so the request cannot immediately recreate namespaced affinity on it.
- A rolling older replica ignores the scope column. The scoped marker therefore leaves the historical timestamp tombstone empty, causing the older reader to keep the retained hard owner instead of treating the row as globally ownerless.

## Example

A process session is mapped to account A, but A is locally capped. A self-contained request may run on account B while the process-session row continues to point to A. If B produces `resp_123`, a follow-up carrying `previous_response_id=resp_123` follows B's response-owner index. If the same follow-up also references a file pinned to A, it fails with `continuity_owner_conflict` rather than choosing either source. By contrast, a first-turn request carrying only an opaque `file_external` ID that has no live codex-lb pin remains eligible for ordinary routing and is forwarded verbatim.

For an explicit restart example, session `thread-1` has a raw legacy mapping to
quota-exceeded account A while account B is active. Codex resends the complete
account-neutral thread under `thread-1`, without previous-response or
conversation continuity, and includes its goal-continuation marker. The proxy
marks the still-current A mapping abandoned for process-session interpretation,
selects B, and records subsequent session/response continuity on B. An explicit
turn-state request using the same raw text still resolves A. If A recovers or
the row is rebound before the marker commits, the compare-and-set misses and
the request remains fail-closed.

## Operational Notes

No schema or setting migration is required for bare-session spillover. Namespaced rows appear lazily, and old raw rows age out only through existing operational controls. Rollback simply removes the spillover capability and leaves both row forms readable.

Goal-restart recovery adds no setting. Its nullable abandonment-scope migration
requires no backfill: a historical non-null timestamp with NULL scope remains
global, while `session_header` preserves an equal explicit turn-state owner.
Source-qualified markers leave the legacy timestamp NULL, so an older binary
safely restores conservative hard ownership during rollback. Dropping the scope
column loses only restart-recovery state; it does not make the retained owner
mobile.
