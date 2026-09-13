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
affinity and upstream prompt-cache routing can coexist. A client-supplied
`prompt_cache_key` MUST be forwarded unchanged and MUST NOT be used as thread
identity.

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

### Requirement: HTTP Responses routes preserve upstream websocket session continuity

When serving HTTP `/v1/responses` or HTTP `/backend-api/codex/responses`, the
service MUST preserve upstream Responses websocket session continuity on a
stable per-session bridge key instead of opening a brand new upstream session
for every eligible request. For backend Codex requests carrying `thread-id`,
the canonical bridge key MUST use the same derived logical thread identity used
for account locality and compact routing. Otherwise the bridge key MUST use an
explicit session/conversation header when present, then normalized
`prompt_cache_key`, deriving a stable key from the existing cache-affinity
inputs when the client omits one. While bridged, the service MUST preserve the
external HTTP/SSE contract, continue request logging with `transport = "http"`,
and keep requests from different bridge keys isolated.

An established live or durable thread bridge is hard continuity. The bridge
MUST retain request-scoped fork lanes for concurrent unanchored requests and
MUST preserve exact turn-state and previous-response aliases. A request with
`thread-id` MUST NOT fall back to the legacy canonical key derived from process
session and `prompt_cache_key`, because current Codex may share both across
siblings. It MAY recover an old bridge only through an exact hard alias.
Authenticated forwarded affinity kind/key values MUST remain verbatim and MUST
NOT be namespaced or hashed again.

#### Scenario: bridge forwards hard continuity keys to the owner replica

- **WHEN** operators configure multiple eligible bridge instance ids
- **AND** a request uses a bridge key derived from `x-codex-turn-state`, an explicit legacy session header, or `thread-id`
- **AND** that request lands on a non-owner instance
- **THEN** the service MUST forward the request internally to the owner replica
- **AND** it MUST NOT return a topology-bearing `bridge_instance_mismatch` error to the client for that owner mismatch alone

#### Scenario: gateway-style prompt-cache bridge requests tolerate wrong-replica arrival

- **WHEN** a request uses a bridge key derived only from `prompt_cache_key` or a derived prompt-cache key
- **AND** that request lands on a non-owner instance
- **THEN** the service MAY create or reuse a local bridge session on that instance
- **AND** it MUST treat the owner mismatch as a locality miss instead of a continuity failure

#### Scenario: forwarded bridge requests fail closed when owner forwarding loops

- **WHEN** a forwarded hard-continuity bridge request reaches another non-owner replica
- **THEN** the service MUST fail the request with a generic 5xx bridge-forward error
- **AND** it MUST NOT attempt another owner handoff

#### Scenario: local restart orphan is recovered by the replacement instance

- **WHEN** a single local bridge instance is replaced while durable hard-continuity ownership still references the old instance id
- **AND** the old owner has no distinct active forwarding endpoint from the current replacement instance
- **THEN** the replacement instance MUST treat the row as restart-orphaned and may claim durable ownership locally
- **AND** same-account takeover MUST preserve the latest persisted response anchor until a replacement response id is recorded
- **AND** normal client retries MUST NOT be stranded waiting for the old instance lease to expire

When request aliases resolve to different durable rows for the same account,
an explicitly requested previous-response alias MUST select its row even if
that row has since advanced to a newer response id. Without an explicitly
resolved previous-response alias, recovery MUST select the freshest row that
contains a persisted response anchor rather than using alias enumeration order.

#### Scenario: requested durable response alias survives same-account row divergence

- **GIVEN** turn-state and previous-response aliases resolve to different durable rows for the same account
- **AND** the request names the previous-response alias whose row has since advanced to a newer response id
- **WHEN** the service resolves durable continuity
- **THEN** it selects the row resolved by the requested previous-response alias
- **AND** it preserves that row's latest persisted response anchor

#### Scenario: Sequential siblings use distinct canonical bridges

- **GIVEN** root and child requests share process session and `prompt_cache_key`
- **AND** they carry different `thread-id` values
- **WHEN** the child starts after the root request completes
- **THEN** the child uses a different canonical bridge identity
- **AND** another child request reuses only the child's lane

#### Scenario: Old shared canonical lane is not a thread fallback

- **GIVEN** an old bridge exists under `(session-id, prompt_cache_key)`
- **WHEN** a request carries a new thread identity but no exact turn-state or previous-response alias
- **THEN** it does not attach to the old shared lane
- **AND** it creates or reuses its thread-canonical lane

## ADDED Requirements

### Requirement: Thread-goal routing uses payload thread identity

Thread-goal get, set, and clear operations carrying a nonblank payload
`threadId` MUST select account locality from that exact thread identity,
combined with the process session when available. An explicit client turn
state remains hard continuity and MUST retain its existing precedence or
conflict behavior. Other generic request headers MUST NOT cause one sibling
thread's goal operation to follow another sibling's locality. Existing
protocol forwarding and error behavior MUST remain unchanged.

#### Scenario: Sibling goal operations follow their own threads

- **GIVEN** sibling threads share a process session but have distinct `threadId` values
- **WHEN** each invokes thread-goal get, set, or clear
- **THEN** each operation uses its own bounded thread locality
