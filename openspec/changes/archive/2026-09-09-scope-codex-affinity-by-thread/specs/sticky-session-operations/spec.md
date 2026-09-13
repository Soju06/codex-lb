## MODIFIED Requirements

### Requirement: Bare process-session cap spillover is non-mutating

The system MUST parse process-session and thread headers independently. A bare
process-session mapping and a bounded thread-local mapping MUST use distinct,
header-inaccessible storage identities, so a client-supplied hard turn-state
value cannot alias either derived soft row. A current replica MUST consult a
legacy raw Codex-session key independently even when a namespaced process or
thread row exists. Any raw hit MUST take precedence as hard ownership. If a
resolved file, response, bridge, or other exact owner conflicts with that raw
legacy owner, the request MUST fail closed without creating or rewriting any
of those rows.

A missing thread row MAY use an eligible process-session soft row as its
initial placement preference. If that process row is missing, the first
admitted thread MUST initialize it with insert-if-absent and MUST persist its
own bounded thread row. A concurrent or later thread MUST NOT overwrite that
first-writer process preference. Account-cap spillover or later thread movement
MUST NOT rewrite or delete the process-session mapping or a sibling's thread
mapping. A provisional recovery-probe reservation MUST NOT initialize a
missing process preference, because its thread mapping may still require
rollback and a probing account is not a stable process default. A later normal
admission MAY initialize the missing process preference.

When the mapped account for a bare process-session key is locally capped and
another eligible account is selected, the spillover MUST apply only to that
request. Selection MUST NOT update or delete the stored process-session mapping
because of account-cap spillover. If the mapped account is below cap, normal
sticky selection MUST retain it.

#### Scenario: Capped bare-session owner spills without rebinding

- **GIVEN** a bare process-session mapping points to account A
- **AND** account A is locally capped
- **AND** account B is eligible and below cap
- **WHEN** a self-contained pre-visible request is selected
- **THEN** the request uses account B
- **AND** the stored process-session mapping still points to account A

#### Scenario: Unsaturated bare-session owner retains locality

- **GIVEN** a bare process-session mapping points to eligible account A below its local caps
- **WHEN** a self-contained request is selected
- **THEN** the request uses account A
- **AND** the mapping remains unchanged

#### Scenario: Equal session and turn-state values remain isolated

- **GIVEN** a process-session header and an explicit turn-state header have equal text values
- **WHEN** their affinity mappings are resolved
- **THEN** the process-session mapping uses a source-separated opaque key
- **AND** the explicit turn-state mapping continues to use the legacy raw key as hard ownership

#### Scenario: Derived soft key cannot be reused as raw hard turn state

- **GIVEN** a process-session or thread value has a derived internal storage key
- **WHEN** a client submits the visible representation of that key as a turn-state header
- **THEN** header normalization cannot reproduce the internal storage identity
- **AND** hard turn-state selection cannot read or rewrite the soft row

#### Scenario: Legacy raw mapping remains hard

- **GIVEN** a legacy replica persisted a raw Codex-session mapping
- **WHEN** a current replica receives the matching process or legacy thread header
- **THEN** it does not reinterpret or mutate the legacy raw row as spillable affinity
- **AND** mixed-version operation remains fail-closed for that row

#### Scenario: Coexisting legacy and namespaced rows prefer hard ownership

- **GIVEN** mixed-version replicas created a raw row and a namespaced process or thread row for the same request identity
- **AND** the rows point to different accounts
- **WHEN** a current replica selects the request
- **THEN** the raw row's account is treated as the hard owner
- **AND** neither row is deleted or rewritten by account-cap spillover

#### Scenario: Legacy hard owner conflicts with resolved owner

- **GIVEN** a raw legacy session row points to account A
- **AND** a file, previous response, or bridge resolves to account B
- **WHEN** the request is routed
- **THEN** the service fails with `continuity_owner_conflict`
- **AND** it neither bypasses nor rewrites the raw row

#### Scenario: Process preference seeds only the new thread

- **GIVEN** a process-session soft row points to account A
- **AND** no bounded row exists for thread T
- **WHEN** T is admitted on account A or a safely selected alternate
- **THEN** the admitted account is persisted under T's bounded key
- **AND** the process-session row remains unchanged

#### Scenario: Missing process preference is initialized once

- **GIVEN** no process-session mapping exists
- **WHEN** the first thread is admitted on account A and a concurrent or later thread is admitted on account B
- **THEN** insert-if-absent preserves the first persisted process owner
- **AND** each thread persists only its own bounded locality after that initialization

#### Scenario: Provisional probe placement does not escape rollback

- **GIVEN** neither process nor thread has a bounded mapping
- **WHEN** a probing-account placement persists provisionally and then loses its runtime commit
- **THEN** its thread mutation is restored
- **AND** no immutable process preference is left behind

#### Scenario: Legacy raw owner wins over thread locality

- **GIVEN** a raw legacy Codex row points to account A
- **AND** a bounded thread row points to account B
- **WHEN** the request is routed
- **THEN** the raw row remains hard ownership evidence and account A wins
- **AND** neither mapping is rewritten to reconcile the disagreement

### Requirement: Unanchored process-session concurrency uses independent bridge lanes

When multiple Responses requests share a process-level session header but
carry neither `previous_response_id` nor nonblank turn-state continuity, the
service MUST NOT queue an independent request behind an active response-create
gate. If the canonical bridge is still being created, reserved by another
request before submit, already has a visible request, or belongs to a different
model class, the service MUST create a server request-scoped bridge lane. The
lane identity MUST NOT depend on a client-controlled request ID. The fork MUST
leave the canonical bridge and its model metadata unchanged.

When such requests carry nonblank `thread-id`, each thread MUST have a stable
canonical bridge identity derived from process and thread identity regardless
of `prompt_cache_key`; distinct threads MUST remain isolated even when they
execute sequentially, and repeated requests from one thread MUST retain one
identity. Requests without `thread-id` MUST retain the legacy session-header
identity, including the established explicit-prompt-cache composition.

A pre-submit handoff reservation MUST protect its bridge from idle pruning and
capacity eviction, and any cancellation or error between lookup and visible
submission MUST release it. Owner forwarding MUST preserve whether a
session-header, thread-header, or internal-fork request was unanchored instead
of treating a proxy-generated downstream turn-state as an explicit client
anchor, but MUST NOT attach that v2-only state to prompt-cache or unrelated
affinity families. It MUST fail closed when a mixed-version hop cannot
authenticate required unanchored state. The v2 primary signature MUST bind
whether client-IP metadata was present, while the companion signature MUST bind
its value. When the canonical owner itself creates a fork for a forwarded
request, it MUST own that fork locally instead of re-hashing it into another
forwarding hop. Explicitly anchored owner forwards MUST retain the
legacy-compatible primary signature during rolling upgrades, and a receiving
instance MUST reject ambiguous delimiter-bearing legacy fields. Durable aliases
derived from the forked lane MUST retain hard owner and account continuity. If
durable ownership fencing rejects a stale owner's new alias, the stale owner
MUST remove the matching local alias without removing a newer local
generation's mapping.

#### Scenario: sequential child agent does not reuse parent bridge history

- **GIVEN** a parent and child Codex agent share one process session and `prompt_cache_key`
- **AND** each agent supplies its own stable `thread-id`
- **WHEN** the child starts after the parent's visible request has completed
- **THEN** the child uses a different bridge identity from the parent
- **AND** another request from that same child keeps the child's bridge identity

#### Scenario: Background requests do not block behind a foreground turn

- **GIVEN** a foreground request is active on a session-header or thread-header bridge
- **WHEN** two unanchored background requests arrive with the same canonical identity
- **THEN** each background request uses an independent response-create gate
- **AND** neither request waits for the foreground response to complete
- **AND** the foreground bridge's model metadata remains unchanged

#### Scenario: Lookup-to-submit requests remain isolated

- **GIVEN** an unanchored request has reserved an idle canonical bridge but has not yet made queued activity visible
- **WHEN** another unanchored request arrives with the same canonical identity and client request ID
- **THEN** the second request uses a distinct server-scoped bridge lane
- **AND** it does not reuse the reserved canonical bridge

#### Scenario: Durable refresh publishes the handoff reservation

- **GIVEN** an unanchored request reuses an idle durable canonical bridge
- **WHEN** refreshing the durable lease yields before lookup returns
- **THEN** the canonical bridge is already reserved for that request
- **AND** a concurrent unanchored request uses a distinct server-scoped lane

#### Scenario: Cancelled pre-submit handoff does not strand a reservation

- **GIVEN** an unanchored request is reusing an idle canonical bridge
- **WHEN** the request is cancelled after claiming the bridge but before queued activity becomes visible
- **THEN** the canonical bridge remains unreserved
- **AND** later requests are not forced onto fork lanes by the cancelled lookup

#### Scenario: Payload preparation failure does not strand a reservation

- **GIVEN** an unanchored request has reserved an idle canonical bridge
- **WHEN** anchor injection, trimming, or payload validation fails before submission
- **THEN** request-scope cleanup releases the reservation
- **AND** later requests may reuse the canonical bridge

#### Scenario: Remote owner preserves unanchored concurrency

- **GIVEN** an unanchored request is forwarded to the canonical bridge owner
- **AND** the proxy generated a downstream turn-state for response aliasing
- **WHEN** the owner receives the forwarded request while the canonical lane is active
- **THEN** the owner still treats the request as unanchored
- **AND** the request uses an independent bridge lane
- **AND** the pre-submit handoff remains reserved until submission becomes visible

#### Scenario: Owner-side fork does not start a second forwarding hop

- **GIVEN** an unanchored request has reached its canonical owner
- **AND** that owner creates an independent fork because the canonical lane is active
- **WHEN** rendezvous hashing the generated fork key would select another instance
- **THEN** the canonical owner creates and durably claims the fork locally
- **AND** the request is not rejected as a forwarding loop

#### Scenario: Blank turn-state is not an anchor

- **GIVEN** a request has process/thread identity and an empty or whitespace-only turn-state header
- **WHEN** the request is forwarded to its owner
- **THEN** the signed forwarding context marks the original request as unanchored
- **AND** the generated downstream turn-state does not collapse it onto the canonical gate

#### Scenario: Forwarding downgrade fails closed

- **GIVEN** an owner-forward request requires unanchored concurrency semantics
- **WHEN** the signed unanchored boolean is changed, removed, or repacked into affinity fields, or either instance only supports the legacy signature
- **THEN** the owner-forward hop fails closed
- **AND** the request is not attached to the shared canonical response-create gate

#### Scenario: Anchored forwarding remains rolling-upgrade compatible

- **GIVEN** an owner-forward request carries explicit previous-response or turn-state continuity
- **WHEN** the origin and owner run different bridge protocol versions
- **THEN** the primary signature remains valid under the legacy contract
- **AND** the anchored request can continue without weakening unanchored fail-closed behavior

#### Scenario: Prompt-cache forwarding remains rolling-upgrade compatible

- **GIVEN** an unanchored first-turn request uses a prompt-cache affinity lane
- **WHEN** that request is forwarded to its canonical owner
- **THEN** the origin does not attach session/thread-header unanchored v2 state
- **AND** an older owner may accept the legacy-compatible forwarding contract

#### Scenario: Legacy session-header canonical lane proves its turn-state anchor

- **GIVEN** a legacy-signed owner forward has no previous-response ID and its durable canonical key is still `session_header`
- **WHEN** its forwarded turn state is a registered durable alias for that exact canonical lane
- **THEN** the current owner accepts it as anchored continuity
- **AND** an unknown turn state or an alias for another canonical lane fails closed with `bridge_forward_upgrade_required`

#### Scenario: Legacy proof precedes compact and bridge fallback branches

- **GIVEN** a legacy-signed owner forward requires turn-state anchor proof
- **WHEN** the request contains a terminal compaction trigger or bypasses the websocket bridge
- **THEN** exact alias proof runs before compact, HTTP fallback, admission, or upstream work

#### Scenario: Current origin proves a turn-state alias before legacy owner forwarding

- **GIVEN** a current origin resolves a nonblank turn state only through a shared `session_header` durable lane
- **WHEN** that request would be forwarded to another owner with the legacy signature contract
- **THEN** the origin proves an exact turn-state alias row for that canonical lane before sending the owner request
- **AND** an unknown alias fails closed with `bridge_forward_upgrade_required`

#### Scenario: Latest-state metadata is not proof of alias registration

- **GIVEN** a durable session records a latest turn state but has no matching turn-state alias row
- **WHEN** that value is presented by a legacy-signed owner forward
- **THEN** the owner rejects it with `bridge_forward_upgrade_required`

#### Scenario: Stale owners cannot register continuity aliases after takeover

- **GIVEN** durable ownership advanced to a new owner epoch
- **WHEN** the stale owner attempts to register a turn-state or previous-response alias with its old epoch
- **THEN** alias registration writes nothing
- **AND** the stale owner removes the rejected value from its local alias index
- **AND** a newer local generation's mapping for the same value remains intact
- **AND** the stale value cannot satisfy legacy anchor proof

#### Scenario: Ambiguous legacy signature fields fail closed

- **GIVEN** a legacy owner-forward signature contains a delimiter in any signed header field
- **WHEN** field boundaries are repacked without changing the legacy joined byte string
- **THEN** a current owner rejects the forwarding context as invalid
- **AND** the repacked affinity kind cannot weaken hard continuity

#### Scenario: V2 client-IP metadata cannot be removed or blanked

- **GIVEN** an unanchored v2 owner-forward request carries signed client-IP metadata
- **WHEN** both client-IP headers are removed, the value is blanked, or the value is changed
- **THEN** the owner rejects the forwarding context as invalid
- **AND** a genuinely no-IP v2 request remains valid

#### Scenario: Durable fork continuation remains owner-bound

- **GIVEN** a forked lane has produced a durable turn-state or previous-response alias
- **WHEN** a later request resolves that alias on another instance
- **THEN** the request follows the hard owner-bound continuity path
- **AND** the original account binding is preserved

#### Scenario: Explicit continuation is not split

- **WHEN** a request carries `previous_response_id` or a turn-state header
- **THEN** the service keeps the request on the hard owner-bound continuity path
- **AND** it does not apply unanchored parallel-session isolation
