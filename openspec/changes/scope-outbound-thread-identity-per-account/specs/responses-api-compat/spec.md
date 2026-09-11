## MODIFIED Requirements

### Requirement: Use prompt_cache_key as OpenAI cache affinity
For OpenAI-style `/v1/responses`, `/v1/responses/compact`, and chat-completions requests mapped onto Responses, the service MUST treat a non-empty `prompt_cache_key` as the bounded upstream account affinity key for prompt-cache correctness even when a `session_id` header is present. OpenAI-style route wiring MUST NOT upgrade those requests to durable `CODEX_SESSION` affinity by default. This affinity MUST apply even when dashboard `sticky_threads_enabled` is disabled, and the stored affinity MUST expire after the configured freshness window so older keys can rebalance. The `prompt_cache_key` used for affinity is the account-neutral one: while `account_scoped_thread_identity_enabled` is off the service MUST continue forwarding that same value upstream unchanged, and while it is on the service MUST forward an account-scoped derivative of it computed at dispatch, stable for the lifetime of a given (serving account, logical thread) pair. In both cases the value used for affinity and routing MUST remain the account-neutral one, so the scoped rewrite cannot feed back into account selection. The freshness window MUST come from dashboard settings so operators can adjust it without restart.

#### Scenario: OpenAI-style route ignores session header for durable codex-session pinning
- **WHEN** a client sends `/v1/responses` or `/v1/responses/compact` with a non-empty `session_id` header and no explicit sticky-thread mode
- **THEN** the service does not persist a durable `codex_session` mapping solely from that header
- **AND** bounded prompt-cache affinity behavior remains in effect

#### Scenario: dashboard prompt-cache affinity TTL is applied
- **WHEN** an operator updates the dashboard prompt-cache affinity TTL
- **THEN** subsequent OpenAI-style prompt-cache affinity decisions use the new freshness window


#### Scenario: Scoped forwarding leaves the affinity key account-neutral

- **GIVEN** `account_scoped_thread_identity_enabled` is on
- **AND** two consecutive turns of one thread are served by two different accounts
- **WHEN** each turn is routed and dispatched
- **THEN** both routing decisions use the identical account-neutral `prompt_cache_key`
- **AND** each upstream request carries that key scoped to the account serving it
- **AND** a later turn returning to the first account carries the first account's scoped value again

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
affinity and upstream prompt-cache routing can coexist. While `account_scoped_thread_identity_enabled` is off, a
client-supplied `prompt_cache_key` MUST be forwarded unchanged. While it is
on, the outbound `prompt_cache_key` and the accepted process-session and
thread headers MUST be rewritten at dispatch into the selected account's
namespace, deterministically and identically for every turn that account
serves of that thread. In both cases the `prompt_cache_key` MUST NOT be used
as thread identity, and the value that selection and continuity storage see
MUST remain the account-neutral one the client supplied or the service
derived. `x-codex-turn-state` and `previous_response_id` MUST NOT be
rewritten under any setting.

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


#### Scenario: Account-scoped dispatch does not change the selection key

- **GIVEN** `account_scoped_thread_identity_enabled` is on
- **WHEN** a backend Codex request with process-session and thread headers is routed
- **THEN** the bounded locality key is computed from the client-supplied identity, unscoped
- **AND** the upstream request carries the scoped identifiers instead
- **AND** the request's `x-codex-turn-state` reaches upstream byte-for-byte unchanged
