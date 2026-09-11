## MODIFIED Requirements

### Requirement: Codex backend session_id preserves account affinity

When a backend Codex Responses or compact request includes a nonblank
`thread-id`, the service MUST use a source-separated bounded key derived from
the independently parsed process session and thread identity for soft account
locality. If the thread has no mapping, selection MUST first prefer an eligible
source-separated process-session mapping and then persist the admitted thread
mapping. If no process-session mapping exists, the first admitted thread MUST
initialize that soft process preference atomically without overwriting a
concurrent or later first writer, unless its account is admitted only through
a recovery-probe reservation. A recovery-probe admission MUST NOT initialize
the immutable process preference; its reversible thread row MAY be persisted
independently until a normal admission establishes the process default.

When `thread-id` is absent, a non-empty accepted process-session header MUST
retain its established account-affinity behavior. Accepted process-session
headers are `session_id`, `session-id`, `x-codex-session-id`, and
`x-codex-conversation-id`, in that priority order. A client-supplied nonblank
`x-codex-turn-state` remains a more specific hard continuity key. If the
request lacks a client-supplied `prompt_cache_key`, the service MUST derive and
attach a stable `prompt_cache_key` before upstream forwarding so account
affinity and upstream prompt-cache routing can coexist; the derived value MUST
be thread-anchored as described in "Unanchored Responses turns are anchored to
a verified thread key", and when that derivation reports the turn unanchorable
the attached value MUST still be stable for the (model class, API key) pair but
MUST NOT be used as a sticky routing key. A client-supplied `prompt_cache_key`
MUST be forwarded unchanged and MUST NOT be used as thread identity.

A turn state synthesized by the proxy for the current downstream WebSocket
handshake MUST NOT override client-supplied process/thread identity or a
prompt-cache key for routing or WebSocket continuity selection. The proxy MUST
seed WebSocket continuity storage under that synthesized turn state so a later
client echo can reuse the completed-turn owner. The proxy MUST continue to
forward that synthesized turn state upstream. A turn state sent by the client,
including one that the proxy generated and the client later echoed, remains a
client-supplied turn-state affinity key.

When a WebSocket handshake has neither a client-supplied turn state nor an
accepted process/thread identity, the proxy MUST store its generated turn state
as the WebSocket continuity key. A later connection that echoes that accepted
value MUST recover the same continuity state. Direct WebSocket retained
response, input-prefix, Responses Lite, and unresolved-tool state MUST use the
derived thread identity plus API-key scope, with count-bounded storage.
Request-log conversation grouping MUST continue to use raw `thread-id`.

#### Scenario: Backend Codex request derives prompt_cache_key before codex-session routing

- **WHEN** `/backend-api/codex/responses` is called with `session_id` and without `thread-id` or `prompt_cache_key`
- **THEN** the routing decision retains process-session `codex_session` affinity
- **AND** the forwarded upstream payload includes a derived stable `prompt_cache_key`

#### Scenario: backend WebSocket reconnect retains session affinity despite a generated turn state

- **WHEN** two backend Codex Responses WebSocket connections include the same process session and `thread-id` and omit `x-codex-turn-state`
- **AND** the proxy generates a distinct turn state for each handshake
- **THEN** both account selections use the same bounded thread-local affinity key
- **AND** each generated turn state is still forwarded to the upstream

#### Scenario: echoed generated turn state remains a client continuation key

- **WHEN** a client reconnects with a non-empty `x-codex-turn-state` value it received from an earlier proxy handshake
- **THEN** that turn state remains the routing and WebSocket continuity key ahead of broader process/thread locality
- **AND** full-resend continuity for that echoed turn state can reuse the earlier completed response anchor

#### Scenario: generated turn state seeds continuity without a session header

- **WHEN** a backend Codex Responses WebSocket handshake omits process/thread identity and `x-codex-turn-state`
- **AND** the proxy generates and returns a turn state for that handshake
- **THEN** the proxy stores its WebSocket continuity state under that generated value
- **AND WHEN** a later connection sends that value in `x-codex-turn-state`
- **THEN** it recovers the stored continuity state

#### Scenario: Root and child keep separate locality with one cache hint

- **GIVEN** root and child requests share a process session and explicit `prompt_cache_key`
- **AND** they carry different stable `thread-id` values
- **WHEN** backend Responses or compact routes them
- **THEN** they use different bounded internal thread keys
- **AND** both upstream payloads retain the original `prompt_cache_key`

#### Scenario: New thread inherits process preference without coupling siblings

- **GIVEN** a process-session soft row points to eligible account A
- **AND** a previously unseen thread in that process arrives
- **WHEN** selection admits the request
- **THEN** it prefers account A and persists a bounded row for that thread
- **AND** later movement of that thread does not rewrite the process row or a sibling row

#### Scenario: First thread initializes the process preference

- **GIVEN** a fresh process has no process-session or thread mapping
- **WHEN** its first thread is admitted on account A
- **THEN** it initializes the process preference to A with insert-if-absent
- **AND** a later sibling prefers A without gaining authority to rewrite that process preference

#### Scenario: Exact owner admission still initializes first-thread locality

- **GIVEN** a fresh process has no process-session or thread mapping
- **AND** an exact response, file, or bridge owner requires account A
- **WHEN** the first thread is admitted on account A through that hard owner
- **THEN** the thread row and absent process preference are persisted atomically
- **AND** the process preference remains insert-only if another thread already initialized it

#### Scenario: Recovery probe does not seed the process

- **GIVEN** a fresh process has no process-session mapping
- **WHEN** a thread is selected on probing account A through a recovery reservation
- **THEN** account A is not published as the immutable process preference
- **AND** a failed reservation commit can restore the reversible thread placement

#### Scenario: Direct WebSocket siblings do not share replay state

- **GIVEN** sibling threads share one process session and cache key
- **WHEN** each uses direct WebSocket Responses and one reconnects
- **THEN** retained response, prefix, Lite, and pending-tool state is read only from that thread
- **AND** the reconnect cannot inject or replay its sibling's state

#### Scenario: Unknown exact turn does not borrow broader thread replay

- **GIVEN** a direct WebSocket thread has retained replay or tool state
- **WHEN** a request supplies a nonblank client turn state with no exact in-memory alias
- **THEN** it does not reuse or replace the broader thread state
- **AND** only a previously resolved exact alias may refresh the thread alias

#### Scenario: Unanchorable backend request attaches a stable key without a sticky row

- **WHEN** `/backend-api/codex/responses` is called with no `thread-id`, no process-session header and a body with nothing to anchor
- **THEN** the forwarded upstream payload still includes a stable derived `prompt_cache_key`
- **AND** no prompt-cache sticky mapping is written for that request

## ADDED Requirements

### Requirement: Unanchored Responses turns are anchored to a verified thread key

When a `/v1/responses`, `/v1/responses/compact`, or backend Codex Responses
request carries no client-supplied `prompt_cache_key` and OpenAI cache affinity
is enabled, the service MUST derive the key by anchoring the turn to a
process-local thread identity rather than by hashing truncated request text.
The derivation MUST reuse the key it previously minted for a thread when, and
only when, the new turn's input items exactly extend that thread's recorded
item sequence: the recorded sequence, from some offset to its end, MUST equal
the head of the new turn's items, with at least two items matched. Byte-equal
re-derivation of the same body MUST return the same key. No partial, fuzzy,
truncated-prefix, or summary-aware match may reuse a key.

Anchor identity MUST be scoped by API key, model class, and the **complete**
`instructions` value, so two threads whose instructions differ only past a
truncation boundary never share a key. A turn that does not verifiably extend
any recorded thread MUST mint a new key; in particular a compacted or
summarized turn MUST mint a new key rather than be matched heuristically.

A turn with nothing to anchor — a non-list input, or fewer than two input
items — MUST be reported unanchorable. The service MUST still attach a derived
`prompt_cache_key` that is stable for the (model class, API key) pair, MUST NOT
attach a per-request random value, and MUST NOT supply a sticky routing key for
that request, so no single-use sticky mapping is written.

Anchor state MUST be process-local and bounded by a documented maximum number
of tracked threads, a documented maximum retained items per thread, and a
documented maximum index size, all enforced by least-recently-used eviction,
and MUST expire at the configured `openai_cache_affinity_max_age_seconds`
freshness window. Losing anchor state (eviction, expiry, restart, or a
blue/green swap serving both colors) MUST be safe: the next turn mints a new
key.

Every resolution MUST report its outcome — client-supplied, cache affinity
disabled, anchor reuse, new anchor, anchor reset, or unanchorable — on the
request-shape diagnostic and as a counter, so the anchored share of unanchored
traffic is observable without a database query.

Keys minted by this derivation MUST carry a version prefix that distinguishes
them from the retired content-hash shape, and stale non-expiring
`sticky_thread` mappings written under the retired shape MUST be removed by a
bounded background sweep.

#### Scenario: Appended turns reuse one anchor

- **GIVEN** a `/v1/responses` request with no continuity identifier and at least two input items
- **WHEN** the client sends later turns that append items to the same transcript
- **THEN** every turn resolves to the key minted for the first turn
- **AND** each later turn reports the `anchor_hit` outcome

#### Scenario: Trimmed leading history keeps the anchor

- **GIVEN** a thread has an anchor recorded for its transcript
- **WHEN** the next turn drops the oldest input items and appends new ones, keeping a contiguous recent window of at least two recorded items
- **THEN** it resolves to the same key
- **AND** reports the `anchor_hit` outcome

#### Scenario: Two threads sharing their opening item stay separate

- **GIVEN** two threads from one API key whose first input item is byte-identical
- **AND** whose following items differ
- **WHEN** each thread's turns are resolved
- **THEN** the two threads hold different keys for every turn

#### Scenario: A compacted turn mints a new anchor and says so

- **GIVEN** a thread has an anchor recorded for its transcript
- **WHEN** the next turn replaces the middle of that transcript with a summary
- **THEN** it mints a new key
- **AND** reports the `anchor_reset` outcome rather than `anchor_hit`

#### Scenario: An unanchorable turn is reported, not randomly keyed

- **GIVEN** a request with cache affinity enabled and an empty input list
- **WHEN** the same request shape is resolved twice
- **THEN** both attach the same derived `prompt_cache_key` upstream
- **AND** both report the `unanchorable` outcome and supply no sticky routing key

#### Scenario: Anchor state stays bounded

- **GIVEN** more distinct unanchored threads arrive than the documented anchor capacity
- **WHEN** their turns are resolved
- **THEN** the number of tracked anchors and the index size stay at or below their documented maxima
- **AND** an evicted thread's next turn mints a new key instead of failing

#### Scenario: Retired content-hash sticky_thread mappings are swept

- **GIVEN** `sticky_sessions` holds `sticky_thread` rows keyed by the retired content-hash shape and idle past the freshness window
- **WHEN** the sticky-session cleanup pass runs as leader
- **THEN** it deletes those rows in bounded batches until none remain
- **AND** it stops sweeping once a pass finds none
