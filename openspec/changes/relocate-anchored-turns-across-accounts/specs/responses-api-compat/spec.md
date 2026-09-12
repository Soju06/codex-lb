# responses-api-compat Delta

## ADDED Requirements

### Requirement: Relocation eligibility has a single decision point

The decision whether an anchored request may be dispatched to a different account, and which request body that dispatch carries, MUST be produced by one shared, transport-independent evaluation. Every transport — direct HTTP streaming, downstream WebSocket, and the HTTP session bridge — MUST obtain its verdict from that evaluation rather than computing its own eligibility. Given equal inputs, all transports MUST reach the same verdict.

The evaluation MUST be pure with respect to the request: loading the durable transcript is the only I/O, it MUST happen in one place, and a failure to load it MUST be treated as "no transcript" rather than as an error that fails the request.

Every verdict MUST record a closed-vocabulary decline reason, and a declined relocation MUST leave the request on exactly the behaviour it has today.

The verdict MUST decline, before consulting any evidence, when downstream-visible output has already been emitted, or when the request is bound to its account by the `single_account` routing strategy, an input-file pin, turn-state ownership, or a session-identity binding. These are ownership facts that no request body can neutralize.

#### Scenario: Transports agree

- **GIVEN** identical request state, anchor, evidence and durable transcript
- **WHEN** the stream path, the WebSocket path and the bridge path each evaluate relocation
- **THEN** all three produce the same verdict, the same body and the same decline reason

#### Scenario: File-pinned and turn-state-owned requests never relocate

- **GIVEN** a request carrying an input-file account pin or a turn-state owner
- **WHEN** relocation is evaluated after a definitive pre-dispatch rejection
- **THEN** the verdict declines with an ownership reason
- **AND** the request keeps today's owner-bound behaviour

#### Scenario: Visible output ends eligibility

- **GIVEN** the client has already received response output for this turn
- **WHEN** the upstream connection then fails
- **THEN** relocation declines
- **AND** the failure is surfaced as it is today

### Requirement: Anchored turns relocate on a rebuilt durable transcript

When an anchored continuation cannot be served by its owner account and the evidence is definitive — an upstream quota or usage-limit rejection, or a confirmed pre-dispatch transport failure, in both cases with no response event emitted and no downstream-visible output — the proxy MUST attempt to rebuild the turn's full conversation from the durable operation spool and dispatch it to another account without the anchor.

The rebuild MUST walk the `parent_response_id` chain from the anchor and assemble the turns oldest first, matching the order the durable repository returns them in, and for each turn MUST combine the stored request body with the stored terminal response output. A rebuild that assembles the chain in any other order MUST be rejected: a chronologically reversed conversation satisfies every structural predicate and fails silently. It MUST be bounded by a maximum turn count and by a maximum byte size measured across the whole transcript, not per turn; a per-turn reading of the bound multiplies the worst case by the turn limit.

A turn counts as settled, and so as material for the rebuild, only when its spool carries a terminal event that reports an answer. A terminal event that reports a failure MUST NOT make a failed turn read as an answered one in the rebuilt conversation. The rebuilt input MUST then be joined to the client's current turn. The join MUST NOT delete, reorder or alter any item the client sent. A rebuild that drops a client item because its content coincides with an item already in the chain is a worse failure than refusing to rebuild at all: the user's message is gone, the result still satisfies every structural predicate, and nothing downstream can detect it.

The proxy MUST therefore establish which shape the client sent rather than infer it from content equality. When the client's input is a continuation delta, the chain supplies the history and the delta is appended unchanged. When it is a full resend, the client's own body is the authority and the chain is not used. When the input is provably neither — a partial restatement whose boundary cannot be established without matching content — the proxy MUST refuse to rebuild and keep today's fail-closed behaviour.

Content comparison MAY be used to *verify* a boundary the shape already established. It MUST NOT be used to *discover* one.

The rebuilt body MUST satisfy the same strict account-neutral predicate a client-supplied full resend must satisfy. The proxy MUST fail closed — leaving today's owner-unavailable behaviour intact — when the transcript is unavailable or incomplete, when any turn lacks a stored request body or a complete event spool, when the parent chain is broken or cyclic, when a terminal response event is missing, when the current input is a scalar string that cannot carry prior context, when a tool call is unsettled, when a declared tool is not portable, or when any account-owned state survives projection.

The proxy MUST NOT substitute a lenient projection when the strict predicate declines. An unclassifiable body MUST keep the request owner-bound.

A relocation performed under this requirement MUST NOT consume the one-shot recovery budget defined by "Fenced one-shot recovery dispatch", because definitive evidence proves the operation was never accepted.

The rebuild requires durable operation material — a stored request body, a complete event spool and a parent link for every turn in the chain. A transport that does not record that material has nothing to rebuild from and MUST keep today's fail-closed behaviour; it MUST NOT relocate on partial context, and it MUST NOT report the absence of a transcript as a rebuild failure. Today only the HTTP session bridge records it.

#### Scenario: A delta continuation survives its exhausted owner

- **GIVEN** a continuation carrying only `previous_response_id` and one new user turn
- **AND** the owner account answers with a usage-limit rejection before any response event
- **AND** the durable chain for that anchor is complete
- **WHEN** relocation is evaluated
- **THEN** the proxy rebuilds the conversation from the spool, drops the anchor, and dispatches to another account
- **AND** the client receives the response rather than an owner-unavailable failure

#### Scenario: A client full resend is not doubled

- **GIVEN** the client resent its whole history rather than a delta
- **WHEN** relocation is evaluated
- **THEN** the client's own body is the authority and the durable chain is not joined to it
- **AND** no turn appears twice

#### Scenario: A coincidental content match never costs the client a message

- **GIVEN** a continuation delta whose first item happens to be byte-identical to the first item of the rebuilt chain, while being genuinely new content the user just sent
- **WHEN** the rebuilt body is assembled
- **THEN** every item the client sent appears in the result
- **AND** the coincidence does not shorten the client's turn

#### Scenario: An unestablishable boundary refuses rather than guesses

- **GIVEN** a client input that is neither a recognised continuation delta nor a recognised full resend
- **WHEN** relocation is evaluated
- **THEN** no rebuild is produced and the request keeps today's fail-closed behaviour

#### Scenario: The byte bound is a whole-transcript bound

- **GIVEN** a chain whose turns are individually within the byte bound but whose total exceeds it
- **WHEN** the rebuild walks the chain
- **THEN** it refuses, rather than admitting every turn because each one fits

#### Scenario: A failed turn is not rebuilt as an answered one

- **GIVEN** a turn whose spool ends in a terminal event reporting failure rather than an answer
- **WHEN** the rebuild reaches that turn
- **THEN** it is not treated as settled material
- **AND** the rebuild fails closed rather than presenting the failure as the assistant's answer

#### Scenario: An incomplete spool fails closed

- **GIVEN** a turn in the parent chain whose event spool is marked incomplete, or whose stored request body is missing
- **WHEN** relocation is evaluated
- **THEN** no rebuilt body is produced
- **AND** the request keeps today's owner-unavailable behaviour

#### Scenario: Unsettled tool state fails closed

- **GIVEN** a rebuilt body whose final turn contains a tool call with no matching output
- **WHEN** the strict account-neutral predicate runs
- **THEN** the rebuild is rejected and the request stays owner-bound

#### Scenario: A transport without durable material keeps failing closed

- **GIVEN** an anchored turn on a transport that records no durable operation material for its parent turns
- **WHEN** its owner account answers with a definitive quota rejection
- **THEN** no rebuild is attempted and the request keeps today's owner-unavailable behaviour
- **AND** the outcome is reported as an absent transcript, not as a failed rebuild

#### Scenario: A deterministic non-quota rejection is not relocated

- **GIVEN** the owner answers with an invalid-request rejection
- **WHEN** relocation is evaluated
- **THEN** the rejection is surfaced
- **AND** no other account is attempted, because another account would reject it identically

### Requirement: Ambiguous eventless dispatches relocate once behind the durable fence

When a dispatch left for upstream and the transport then failed ambiguously — `stream_incomplete`, `stream_idle_timeout`, or `upstream_request_timeout` — the proxy MAY relocate the turn to another account exactly once, and MUST do so only through the atomic one-shot claim defined by "Fenced one-shot recovery dispatch".

Beyond that claim, the proxy MUST require all of the following before relocating, and MUST fail closed when any is absent:

- no downstream-visible output, no recorded response id, and zero spooled events for the operation — a single spooled event is proof that upstream executed the turn, which makes the outcome known rather than ambiguous;
- the elapsed time since dispatch is within a bounded ambiguity window, so a turn that may be mid-execution and about to write its first event is not duplicated;
- the relocated dispatch carries the origin operation's side-effect replay-dedupe identity, so a tool call the original dispatch may already have produced is suppressed on the new account rather than executed a second time. A transport that does not implement that dedupe contract MUST NOT take the fenced lane at all: without it the duplicate this lane knowingly risks is unbounded in kind, not just in tokens. Today only the HTTP session bridge implements it;
- the rebuilt body satisfies the same strict account-neutral predicate required by "Anchored turns relocate on a rebuilt durable transcript".

The one-shot budget MUST be at most one dispatch per operation for the whole of that operation's retention, across every replica and every reconnect. When the claim is refused, the request MUST terminate through the fail-closed outcome its transport already produces today and MUST NOT invent a second dispatch: on the HTTP session bridge that is the `upstream_operation_status_unknown` rejection with its cooldown retry hint; on the direct streaming and WebSocket paths it is the terminal transport failure the client receives today. A refused claim MUST NOT be reported as a pool-exhaustion or usage-limit outcome.

#### Scenario: One ambiguous failure buys one relocation

- **GIVEN** an eventless operation whose transport failed with `stream_incomplete`
- **WHEN** relocation is evaluated and the claim succeeds
- **THEN** the turn is dispatched once on another account
- **AND** a second ambiguous failure for the same operation is refused and terminates through its transport's existing fail-closed outcome without a second dispatch

#### Scenario: A spooled event proves execution and blocks relocation

- **GIVEN** an operation with at least one spooled response event
- **WHEN** the transport fails ambiguously
- **THEN** relocation declines without consuming the claim
- **AND** the request terminates as it does today

#### Scenario: A stale ambiguity is not relocated

- **GIVEN** an eventless ambiguous operation whose dispatch is older than the ambiguity window
- **WHEN** relocation is evaluated
- **THEN** relocation declines
- **AND** the claim is not consumed

#### Scenario: Duplicate side effects are suppressed, not executed

- **GIVEN** a relocated ambiguous dispatch that reproduces a side-effecting tool call the origin dispatch may already have emitted
- **WHEN** the replacement account emits that call
- **THEN** the existing side-effect replay dedupe suppresses it
- **AND** the suppression uses the dedicated terminal failure rather than executing the call twice

### Requirement: A relocation dispatch starts from a fresh spool

Before a relocated dispatch is sent, the proxy MUST clear any partial event spool recorded for that operation, so a transcript rebuilt later cannot concatenate the abandoned attempt's events onto the replacement's. The clear MUST happen in the same atomic step that consumes the replay claim.

#### Scenario: A partial spool cannot leak into the replacement

- **GIVEN** an operation that spooled a partial, non-terminal event stream before its ambiguous failure
- **WHEN** the relocation claim is consumed
- **THEN** the operation's spool is empty before the replacement frame is sent
- **AND** a later transcript rebuild sees only the replacement's events

## MODIFIED Requirements

### Requirement: Durable replay is limited to ambiguous transport outcomes

The proxy MUST consume an `unknown` recovery-journal record for a fresh
account-neutral replay only after an ambiguous transport outcome, represented
by `stream_incomplete`, `stream_idle_timeout`, or
`upstream_request_timeout`, and only before any response event or downstream
output.

An explicit deterministic `response.failed` error MUST settle normally and MUST NOT consume the recovery fence. A deterministic rejection that proves upstream accepted nothing — an upstream quota or usage-limit rejection with no response event and no downstream-visible output — MAY instead relocate through the unfenced lane defined by "Anchored turns relocate on a rebuilt durable transcript", which consumes no replay budget and is rolled back rather than claimed. Every other deterministic rejection, including an invalid-request rejection, MUST NOT be relocated to another account at all.

#### Scenario: Transport ambiguity permits one replay

- **GIVEN** an `unknown` proof-gated journal record exists
- **AND** the upstream closes or times out before any response event
- **WHEN** the bridge handles the ambiguous transport failure
- **THEN** the record is atomically claimed and the request is replayed once
  on a fresh account-neutral upstream session

#### Scenario: Deterministic failure is not replayed

- **GIVEN** an `unknown` proof-gated journal record exists
- **AND** upstream emits an explicit pre-output `response.failed` such as an
  invalid request rejection
- **WHEN** the bridge handles that terminal event
- **THEN** it forwards the terminal failure
- **AND** it leaves the journal available for settlement without replaying on
  another account

#### Scenario: A deterministic quota rejection takes the unfenced lane

- **GIVEN** an anchored turn whose owner answers with a pre-output quota or usage-limit rejection
- **WHEN** the proxy evaluates relocation
- **THEN** the recovery claim is not consumed
- **AND** relocation, if the strict rebuild succeeds, proceeds on the unfenced lane
- **AND** a failure to rebuild leaves the journal available for settlement

### Requirement: Fenced one-shot recovery dispatch

The durable recovery journal MUST persist a one-shot replay budget for every
recovery-safe request. The budget MUST be at most one dispatch per operation for the whole of that operation's retention, across every replica and every reconnect. The budget MUST be consumed atomically when a replay is
claimed for dispatch, together with the spool clear required by "A relocation dispatch starts from a fresh spool", and a caller that proves the replay never reached the
upstream send boundary MUST restore that claim under the same session owner
fence. A replacement session MUST retain or transfer a fenced origin owner
until the claim is rolled back or settled; selecting a replacement or failing
preflight MUST NOT permanently consume an unsent replay.

The claim MUST be refused unless the preconditions in "Ambiguous eventless dispatches relocate once behind the durable fence" hold. A refused claim MUST terminate the request through the fail-closed outcome its transport already produces, without a second dispatch.

#### Scenario: Concurrent reconnects consume one replay

- **WHEN** concurrent reconnects observe the same ambiguous operation
- **THEN** exactly one owner atomically claims the persisted replay budget and
  other reconnects fail closed without dispatching a duplicate

#### Scenario: Pre-dispatch replacement failure restores the budget

- **WHEN** a replay claim is made but replacement admission or preflight fails
  before the exact upstream frame is sent
- **THEN** the claim returns to the available state and the fenced origin
  owner is released only after that rollback succeeds

#### Scenario: Successful replacement settles the origin journal

- **WHEN** a replacement session dispatches the claimed replay and receives a
  terminal response event
- **THEN** settlement uses the retained origin owner fence before releasing it
  and the replay budget cannot be claimed again

#### Scenario: A restored claim is reusable exactly once

- **WHEN** a claim is restored after a pre-dispatch preflight failure
- **THEN** a later ambiguous failure for the same operation may claim it again
- **AND** after that dispatch the budget is exhausted
