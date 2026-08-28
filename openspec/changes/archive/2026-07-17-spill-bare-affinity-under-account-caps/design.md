## Context

`StickySessionKind.CODEX_SESSION` currently covers both process-level session headers and account-owned turn-state continuity. The balancer therefore treats an existing process-session mapping as hard even when the request is self-contained and the mapped account is locally capped.

The rejected prototype moved the persistent mapping after admission. That required a pending compare-and-set token in compact, SSE, direct WebSocket, and HTTP bridge flows. Bridge support then coordinated the sticky row, durable bridge row, and local registry with compensating rollback, while direct WebSocket support could retire a socket shared by unrelated requests. This design removes the mapping transition instead of making that distributed transaction more elaborate.

## Goals / Non-Goals

**Goals:**

- Use an eligible alternate account for pre-visible, self-contained work when a bare process-session owner is locally capped.
- Keep hard continuity fail-closed and give resolved owners precedence over soft locality.
- Keep spillover request-local so transports need no settlement, rollback, publication, or replay state.
- Preserve rolling-upgrade safety between legacy raw Codex-session rows and the new soft source.

**Non-Goals:**

- Move or rewrite the stored process-session owner under cap pressure.
- Switch accounts after a frame/request has entered a shared WebSocket or durable bridge lifecycle.
- Move previous responses, conversations, files, explicit turn state, live/durable bridge sessions, or replay/reattach work between accounts.
- Guarantee successful spillover after a race in a later admission stage.
- Change upstream 429 classification, account cap values, or overload envelopes.

## Decisions

### Treat cap pressure as request-local spillover

The balancer filters a bare session's mapped account through ordinary account caps. If it survives, normal sticky selection retains it. If it is capped and another account survives, the request uses the alternate but the sticky row is neither updated nor deleted.

This deliberately trades some cache locality during sustained pressure for a single-authority design. A response, file, or durable bridge produced by the alternate establishes its own hard ownership through existing indexes. Self-contained work has no correctness need to make the spill account the next soft owner.

Persisting a delayed compare-and-set was rejected because admission completes at different points in each transport and bridge publication spans independent persistent and in-memory authorities. Persisting before admission was rejected because a later cap failure could move affinity to an account that never accepted the request.

### Namespace the soft source while retaining legacy hard keys

Session-header selection uses a source-prefixed SHA-256 key under the existing `codex_session` kind. Its internal prefix begins with LF: HTTP forbids CR/LF in header values and the affinity parsers strip surrounding whitespace, while PostgreSQL and SQLite text keys retain LF. The internal storage namespace is therefore structurally unreachable by a client-supplied raw session or turn-state header even if the derived key is disclosed. Explicit turn-state selection keeps the historical raw key. This prevents soft/hard source collisions without a PostgreSQL enum migration.

Legacy raw rows remain fail-closed. A current replica always consults the raw key even when a namespaced row also exists, because mixed-version replicas can create both rows on different accounts; any raw hit wins as conservative hard ownership. New replicas therefore do not reinterpret or bypass legacy ownership, so rolling upgrades may temporarily provide less spillover but cannot weaken continuity.

Adding a fourth sticky kind was rejected because the PostgreSQL enum would require a migration for an internal source distinction. Reusing raw keys with a capability bit was rejected because a forgotten or incorrect caller flag could weaken an existing hard row.

### Resolve hard ownership before applying soft locality

A resolved preferred owner from previous-response, file, live/durable bridge, or replay state is selected without consulting or mutating a bare-session sticky row. Explicit client turn state remains a hard Codex-session mapping. Conflicting hard signals fail closed through their existing owner-validation paths.

Required owners narrow only effective routing states after ownership ambiguity has been checked against the original model/API-key/security pool. This keeps a preferred file/response/bridge owner from manufacturing uniqueness for an unrelated `conversation`. The same required-owner boundary compares namespaced/raw hard sticky rows and fails closed if they disagree.

Single-account routing follows the same split: it supplies a required routing account without reducing the ownership pool. Direct WebSocket frames re-run this ownership-only decision when they reuse an open socket, because transport reuse proves where bytes can currently be sent, not which account owns a newly supplied `conversation` object.

Every referenced input file is checked before source precedence. Live pins are hard ownership evidence: partial live-pin coverage or pins for different accounts fail closed, and a shared consistency helper rejects disagreement between file, previous-response, turn-state, and bridge owners. When no referenced ID has a live pin, the request retains the established opaque-ID compatibility path because the upload may predate this process or have been registered directly upstream. Selecting the "most recent" live pin or skipping file lookup behind another hard signal was rejected because either approach silently abandons account-scoped state that codex-lb can actually prove.

`conversation` has no dedicated owner index. A request carrying it may use an explicit hard Codex mapping; otherwise it proceeds only when the model/API-key/security-scoped pool contains exactly one account before transient additional-quota availability, retry exclusions, runtime health, budget, or account-cap filtering. A temporarily quota-filtered, excluded, unhealthy, or capped account may still be the real owner, so filtering it out cannot manufacture uniqueness. A bare-session mapping does not prove conversation ownership.

Resolved raw or legacy Codex mappings bypass soft sticky fallback and reallocation entirely. Their owner may be selected or fail closed, but budget pressure and unavailability cannot delete or rewrite the ownership row.

Turn-state ownership is resolved through both the live bridge registry and the durable alias index on every transport. HTTP bridge request lookup similarly gathers turn-state, previous-response, and session aliases before choosing a target. Distinct live sessions conflict even when they happen to use the same account, because the upstream session itself is continuity state. Metadata lookup failure remains fail-closed for explicit hard evidence.

File pins remain process-local, so cross-replica owner forwarding carries the origin-resolved file account in the authenticated full-context signature. The receiver skips its own local pin lookup only when that proof verifies; the legacy primary signature deliberately cannot authorize the additive field, preserving rolling-upgrade compatibility for forwards without file ownership while rejecting unsafe downgrades.

### Stop spillover at transport handoff

Selection may prefilter both stream and response-create capacity before opening an upstream stream. If a later lease acquisition loses a race, the transport returns the existing bounded local-cap error. It does not retire a shared WebSocket, create an unpublished replacement bridge, or replay on another account.

An upstream WebSocket turn-state token is owned by the account that issued it. When a closed socket is replaced for a movable bare-session request, the token is cleared before selecting or connecting a different account; replay/reattach and other owner-bearing stages remain non-movable instead.

This boundary keeps transport state machines unchanged. It accepts that rare late races may underutilize another account for one attempt in exchange for avoiding cross-request interruption and partial publication.

### Keep the feature zero-config

Bare process-session affinity is a cache/locality preference when no hard owner signal exists. Spilling only the current pre-visible request cannot corrupt account-owned state, so the safe behavior is the default and does not need another operator setting.

## Risks / Trade-offs

- **Repeated spill requests may choose different alternates** -> only self-contained work is eligible; the original mapping restores locality when its owner has capacity again.
- **A later admission race can still return local overload** -> the transport fails boundedly instead of performing a stateful account switch after handoff.
- **A new owner source could be mistaken for soft locality** -> the shared service boundary revokes spillover for preferred owners and unknown recovery stages; production comments and product-path tests document the precedence.
- **Mixed versions disagree about the soft namespace** -> legacy raw mappings remain hard, producing conservative overload rather than cross-account continuation.

## Migration Plan

No schema or configuration migration is required. New source-namespaced rows are created lazily. Legacy raw process-session rows remain readable as conservative hard mappings and may age out only through existing operational controls.

Rollback removes the spillover capability and restores the previous fail-closed behavior without data conversion.

## Open Questions

None.
