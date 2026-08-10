# Sticky Session Operations Context

## Purpose and Scope

This capability covers operational control and routing semantics for sticky-session mappings. It distinguishes hard account/session ownership from bounded cache locality, including bare process-session spillover under account caps.

See `openspec/specs/sticky-session-operations/spec.md` for normative requirements.

## Decisions

- Sticky-session rows store an explicit `kind` so prompt-cache cleanup can target only bounded mappings.
- Bare process-session headers use a header-inaccessible, source-separated storage key and are soft only for self-contained pre-visible work.
- Account-cap spillover is request-local: it selects an alternate without deleting or rebinding the process-session row.
- Raw and legacy Codex rows remain hard during rolling upgrades because they may represent explicit turn-state ownership.
- A raw legacy Codex owner can be abandoned only for an explicit goal-continuation restart whose canonical upstream payload passes the account-neutral fresh-replay proof, and only while that owner has a persisted unavailable status. Canonicalization keeps accepted compatibility fields and transport envelopes from changing classification. The compare-and-set tombstone preserves a concurrent rebind or owner recovery.
- The retired owner is excluded from stale account snapshots for the remainder of the request.
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
- Partial file-pin coverage or conflicting hard-owner metadata returns a stable fail-closed error before upstream dispatch; zero file-pin coverage preserves the established opaque-ID forwarding path.
- A turn-state token learned from a retired WebSocket is discarded before a movable bare-session request connects to another account.
- An ordinary same-session request, or a goal-marked request that still carries previous-response, conversation, account-scoped file/image, or unresolved tool state, remains fail-closed on an unavailable raw owner.
- Local caps, retry exclusions, transient runtime health, and budget pressure never authorize legacy hard-owner abandonment.
- Selection inputs can predate the guarded retirement transaction. Once retirement succeeds, the old owner is filtered from those inputs so the request cannot immediately recreate namespaced affinity on it.

## Example

A process session is mapped to account A, but A is locally capped. A self-contained request may run on account B while the process-session row continues to point to A. If B produces `resp_123`, a follow-up carrying `previous_response_id=resp_123` follows B's response-owner index. If the same follow-up also references a file pinned to A, it fails with `continuity_owner_conflict` rather than choosing either source. By contrast, a first-turn request carrying only an opaque `file_external` ID that has no live codex-lb pin remains eligible for ordinary routing and is forwarded verbatim.

For an explicit restart example, session `thread-1` has a raw legacy mapping to
quota-exceeded account A while account B is active. Codex resends the complete
account-neutral thread under `thread-1`, without previous-response or
conversation continuity, and includes its goal-continuation marker. The proxy
tombstones the still-current A mapping, selects B, and records subsequent
session/response continuity on B. If A recovers or the row is rebound before
the tombstone commits, the compare-and-set misses and the request remains
fail-closed.

## Operational Notes

No schema or setting migration is required for bare-session spillover. Namespaced rows appear lazily, and old raw rows age out only through existing operational controls. Rollback simply removes the spillover capability and leaves both row forms readable.

Goal-restart recovery also adds no setting or schema migration. Its tombstones
use the existing `continuity_abandoned_at` lifecycle and remain readable after
rollback.
