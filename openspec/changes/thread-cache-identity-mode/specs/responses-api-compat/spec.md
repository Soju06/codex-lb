# responses-api-compat delta

## MODIFIED Requirements

### Requirement: Use prompt_cache_key as OpenAI cache affinity
For OpenAI-style `/v1/responses`, `/v1/responses/compact`, and chat-completions requests mapped onto Responses, the service MUST treat a non-empty `prompt_cache_key` as the bounded upstream account affinity key for prompt-cache correctness even when a `session_id` header is present. OpenAI-style route wiring MUST NOT upgrade those requests to durable `CODEX_SESSION` affinity by default. This affinity MUST apply even when dashboard `sticky_threads_enabled` is disabled, the service MUST continue forwarding the same `prompt_cache_key` upstream unchanged while the effective thread cache identity mode is `shared` (in `isolated` mode the forwarded copy is account-scoped as defined by "Thread cache identity mode scopes the outbound cache prefix", and the affinity key the service routes on is still the unscoped client value), and the stored affinity MUST expire after the configured freshness window so older keys can rebalance. The freshness window MUST come from dashboard settings so operators can adjust it without restart.

#### Scenario: OpenAI-style route ignores session header for durable codex-session pinning
- **WHEN** a client sends `/v1/responses` or `/v1/responses/compact` with a non-empty `session_id` header and no explicit sticky-thread mode
- **THEN** the service does not persist a durable `codex_session` mapping solely from that header
- **AND** bounded prompt-cache affinity behavior remains in effect

#### Scenario: dashboard prompt-cache affinity TTL is applied
- **WHEN** an operator updates the dashboard prompt-cache affinity TTL
- **THEN** subsequent OpenAI-style prompt-cache affinity decisions use the new freshness window

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
`prompt_cache_key` MUST be forwarded unchanged while the effective thread
cache identity mode is `shared`, MUST be forwarded account-scoped in
`isolated` mode, and MUST NOT be used as thread identity in either mode.
The accepted process-session headers above and `thread-id` are likewise
forwarded unchanged in `shared` mode and account-scoped in `isolated` mode;
`x-codex-turn-state` is never rewritten in either mode.

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

## ADDED Requirements

### Requirement: Thread cache identity mode scopes the outbound cache prefix

The service SHALL support exactly two thread cache identity modes, `shared` and
`isolated`, with `shared` as the code default. The mode exists for cache
isolation and deterministic per-account cache behaviour; it MUST NOT be
documented, justified or presented as a countermeasure to provider-side
correlation.

In `shared` mode the outbound upstream request — its serialized body and its
header list, for HTTP streaming, non-streaming HTTP, the websocket
`response.create` frame and the compact command alike — MUST be byte-for-byte
identical to the request the service would send with no thread cache identity
feature present at all.

In `isolated` mode the service MUST scope exactly three legs of the outbound
request to the selected upstream account, and no others:

1. an outbound `prompt_cache_key` that the request already carries, which is
   made account-specific without growing past the upstream length bound for
   that field — a key that would exceed the bound once scoped MUST be folded
   into a bounded form that still distinguishes both the client key and the
   account, so that a request which was valid in `shared` mode cannot become
   an upstream rejection in `isolated` mode; a request that carries no key
   MUST NOT have one invented;
2. the accepted Codex process-session headers (`session_id`, `session-id`,
   `x-codex-session-id`, `x-codex-conversation-id`) and `thread-id`, each
   rewritten in place so a native client's header order and spelling survive,
   and each rewritten shape-preservingly so a UUID-shaped value stays
   UUID-shaped;
3. a single stable opaque line prepended to the prompt content.

The scope token MUST be derived from the load-balancer account id alone, by a
deterministic function with no clock input and no randomness, so that the same
account always produces the same token and two different accounts produce
different tokens. It MUST NOT incorporate any session, turn, conversation or
request identifier. In particular it MUST NOT be derived from
`x-codex-turn-state`, which the service mints fresh per turn when the client
does not supply one and which would therefore rotate the prefix on every turn.

Consequently, for a fixed account, two consecutive turns of the same session
MUST produce the same scope token, including when the later turn carries no
client-supplied session identifier and including when a different
`x-codex-turn-state` is present.

Content injection MUST be shape-aware and decided from the outbound payload
alone: when top-level `instructions` is a non-empty string the line is
prepended to it; otherwise — which is the responses-lite wire shape, where the
base instructions and the tool bundle travel as the first input items and
`instructions` is the empty string — the line is prepended as a leading `input`
item and `instructions` is left exactly as the client sent it. The service MUST
NOT synthesize a non-empty `instructions` value on a request whose client sent
an empty one.

Scoping MUST be applied at egress only. The service MUST NOT write the scoped
`prompt_cache_key` back onto the request model, because the ingress affinity
resolver routes on that value: an account-dependent affinity key would make
routing circular, would carry the previous account's key into a failover retry
to a sibling, and would break conversation grouping in the request log. After
an `isolated` request completes, the request model's `prompt_cache_key` and the
inbound headers mapping MUST be unchanged.

Injection MUST happen before any size measurement of the request that governs
transport selection or admission, so that no request is validated against a
size it is not actually sent at. Where a transport is pre-selected from the
request model and passed downstream as an explicit choice, that pre-selection
MUST account for the bytes `isolated` mode will add, so a request sitting just
under the websocket payload budget is measured at its egress size.

Scoping the key MUST be unconditional on the value the client supplied: a
client key that happens to resemble an already-scoped value MUST still be
scoped, because forwarding it unchanged would send the identical key on two
accounts. The scoped value MUST be a deterministic function of the client key
and the account, so repeated requests on one account agree.

The service MUST NOT modify `x-codex-turn-state` or `previous_response_id` in
either mode.

`isolated` mode applies to the egress paths that build their upstream payload
from the parsed request model at the point an account is already selected: HTTP
streaming and non-streaming HTTP through the core upstream client, the upstream
websocket `response.create` frame that same client sends, and the compact
command.

It also applies to the **HTTP session bridge**, which is enabled by default and
submits its own serialized request text rather than going through the core
upstream client. The bridge MUST scope the frame it sends and MUST NOT write
the scoped text back into the request state or the fresh upstream request text,
because the durable operation fingerprint has to keep hashing account-neutral
text: a scoped value in that fingerprint would change the operation identity on
every account swap and make the spool lookup miss mid-recovery. Leaving the
bridge unscoped is not a smaller version of the feature but a worse one — a
thread served partly by the bridge and partly by the per-turn bypass would
alternate between a scoped and an unscoped identity turn by turn, so upstream
would see two names for one thread on one account.

It does NOT yet apply to the **direct downstream WebSocket** surface, which
relays the client's frame text verbatim, and no document may describe it as
covering that path. That surface serializes the request text *before* an
account is chosen and then treats that exact text as a dispatch-owner and
replay key, so scoping it means re-preparing the text after account selection,
against the stored-input-context, input-fingerprint and size-guard invariants
that re-preparation already has to respect. That is a separate change. Until it
lands, an operator enabling `isolated` SHALL be told that this path remains
shared, and the observability for the mode SHALL make the applied path
distinguishable.

An unrecognised configured mode — from a typo in the environment variable, a
stale `dashboard_settings` value, or a hand-edited API-key override — MUST
resolve to the next layer and ultimately to `shared`, and MUST NOT fail a
request, the settings API or the API-key listing. The settings surface
constrains this setting's value domain, so an unrecognised stored value MUST be
normalized away before it reaches that surface.

The effective mode MUST be resolved once per request in the service layer,
where the API key and the settings snapshot are both in scope; the core
upstream client MUST NOT read the database to determine it. The resolved mode,
and whether it came from a per-key override, MUST be recorded in the
request-shape trace so an A/B can be sliced after the fact.

#### Scenario: Shared mode sends today's bytes

- **GIVEN** the effective thread cache identity mode is `shared`
- **WHEN** the service builds an upstream Responses request over HTTP, over the
  websocket `response.create` frame, or through the compact command
- **THEN** the serialized body and the outbound header list are byte-for-byte
  identical to the output produced without the feature

#### Scenario: Isolated mode scopes all three legs

- **GIVEN** the effective mode is `isolated` and the request carries a
  `prompt_cache_key` and a `session_id` header
- **WHEN** the request is sent on account A
- **THEN** the forwarded `prompt_cache_key` carries A's scope token
- **AND** the forwarded `session_id` is A-specific and keeps its original shape
- **AND** the prompt content begins with A's stable scope line

#### Scenario: Token is stable across turns of one session

- **GIVEN** the effective mode is `isolated` and turn 1 of a session went out
  on account A
- **WHEN** turn 2 of the same session goes out on account A with no
  client-supplied session identifier and a freshly minted
  `x-codex-turn-state`
- **THEN** the scope token, the scope line and the scoped `prompt_cache_key`
  suffix are identical to turn 1

#### Scenario: Token diverges across accounts

- **GIVEN** the effective mode is `isolated`
- **WHEN** the same session is sent on account A and then on account B
- **THEN** the two requests carry different scope tokens, different scoped
  `prompt_cache_key` values and different scoped session headers

#### Scenario: Responses-lite payload keeps its empty instructions

- **GIVEN** the effective mode is `isolated` and the outbound payload is
  responses-lite, with `instructions` set to the empty string and the tool
  bundle in the input prefix
- **WHEN** the scope line is injected
- **THEN** a leading `input` item carries the line
- **AND** `instructions` is still the empty string

#### Scenario: Turn state and previous response id are untouched

- **GIVEN** a request carrying `x-codex-turn-state` and `previous_response_id`
- **WHEN** it is sent in either mode
- **THEN** both values reach upstream exactly as received

#### Scenario: Request model is not mutated by scoping

- **GIVEN** the effective mode is `isolated`
- **WHEN** a request completes and is then retried on a sibling account after a
  failover
- **THEN** the request model's `prompt_cache_key` is still the unscoped value
- **AND** the retry carries the sibling's scope token, not the first account's

#### Scenario: The HTTP session bridge scopes the frame it sends

- **GIVEN** the effective mode is `isolated`
- **WHEN** a turn is carried by the HTTP session bridge
- **THEN** the frame that crosses the wire carries the scoped identity
- **AND** the request state and the fresh upstream request text keep the
  account-neutral original, so the durable operation fingerprint is unchanged

#### Scenario: Direct WebSocket traffic stays shared for now

- **GIVEN** the effective mode is `isolated`
- **WHEN** a turn is carried by the direct downstream WebSocket surface
- **THEN** that turn is sent with its original cache key, headers and content
- **AND** the limitation is documented as a gap rather than presented as
  isolation

#### Scenario: Unrecognised configured mode degrades to shared

- **GIVEN** `CODEX_LB_THREAD_CACHE_IDENTITY_MODE`, or the
  `dashboard_settings` column, holds a value that is neither `shared` nor
  `isolated`
- **WHEN** the settings are loaded and `GET /api/settings` is called
- **THEN** the effective mode is `shared`, the setting reports its source as
  the inherited layer rather than `dashboard`, and the settings API responds
  normally

#### Scenario: Client key resembling a scoped key is still scoped

- **GIVEN** the effective mode is `isolated` and a client supplies a
  `prompt_cache_key` that already ends with account A's scope token
- **WHEN** the request goes out on account A and the same request goes out on
  account B
- **THEN** neither forwarded key equals the client's key
- **AND** the two forwarded keys differ from each other

#### Scenario: Scoped key of a maximum-length client key stays valid

- **GIVEN** the effective mode is `isolated` and a client sends a
  `prompt_cache_key` already at the upstream length bound
- **WHEN** the request is scoped for account A
- **THEN** the forwarded key is still within the bound
- **AND** it differs from the key the same request would carry on account B
- **AND** it differs from the key a different client key would produce on
  account A

#### Scenario: Compact wire budget sees the injected size

- **GIVEN** the effective mode is `isolated`
- **WHEN** a compact request is validated against the upstream wire budget
- **THEN** the validated payload already contains the injected scope line
