## ADDED Requirements

### Requirement: Materialize complete bridge transcript output

When complete-transcript recovery is enabled and a completed operation's
`response.completed.response.output` is empty, the proxy MUST reconstruct the
terminal output from ordered `response.output_item.done` events in the durable
operation spool before marking the output transcript complete.

The proxy MUST reject reconstruction when the terminal completion is missing,
an output item is malformed or unfinished, or the configured transcript bound
would be exceeded.

The configured transcript byte bound MUST be positive and MUST NOT exceed
128 MiB.

The proxy MUST reject reconstruction when more than one terminal completion is
present, when output-item indexes are sparse or reused, or when a present
terminal `output` value is not a list. When both streamed output items and a
non-empty terminal output are present, each item MUST have matching stable
identity fields (`id`, `call_id`, and `type` when provided) before payload
comparison; an omitted terminal `status` MAY be tolerated.

When complete-transcript recovery is enabled, only after establishing a complete,
unambiguous durable transcript, the proxy MUST make a best-effort attempt to
persist a bounded, self-contained replay-input snapshot for that completed
operation. The snapshot MUST omit stale response anchors and
response-owned item IDs, and MUST NOT delay or fail the live response if it
cannot be built.

Pre-dispatch recovery checkpoint rollback MUST finish before native task
cancellation propagates, and successful rollback MUST clear its in-memory
claim before the remaining submit cleanup completes.
Recovery tasks and parked waits MUST use their owner's injected scheduler and
clock. Terminal settlement MUST NOT enqueue an already published terminal a
second time.
Ambiguous-rebind compensation for complete and unsafe-partial recovery MUST
finish both durable rollback and event-fence restoration before propagating
native cancellation, including repeated cancellation.
If compensation fails, recovery MUST retain the operation identity, claim,
expected generation, and restoration metadata for another fenced cleanup
attempt, without authorizing or dispatching replacement work.
Identity-bearing WebSocket terminal errors MUST settle without transparent
replay or reconnect unless they satisfy the bounded output-free accepted
capacity replay rules in `retry-accepted-output-free-capacity-failures`.
Capacity-wait
elapsed time MUST retain the first monotonic timestamp, including zero,
across successive account-recovery waits.
Public response streams MUST NOT forward response data after the first terminal
event, including late content deltas, nonterminal lifecycle frames, and
top-level `error` terminal events in native mode. SSE
comments and the final `[DONE]` marker MAY still be forwarded.
SDK-enforced contract failures MUST be preceded by exactly one
`response.created`, including failures discovered while reconciling terminal
output. Probe teardown MUST preserve cancellation and log unexpected cleanup
exceptions rather than silently hiding them.
Hosted-tool envelopes MUST be omitted from replay only when their status is
`completed`. Missing, unfinished, or failed hosted-tool status MUST NOT authorize
account-neutral replay from durable output or client-supplied history.

Security-work replay MUST clear the interrupted attempt's pending and added
tool-call manifest entries and invalid-manifest marker before reconnecting.

The retry caller MUST install its refund guard before awaiting a durable
UNKNOWN-operation claim. If cancellation arrives during a successful claim,
the caller MUST restore UNKNOWN and refund its recovery generation before
propagating cancellation without dispatching a replacement request.

Initial admission claims and local replay journal claims MUST also defer
cancellation until their durable result and in-memory ownership are recorded.
A committed pre-dispatch claim MUST be refunded on cancellation.

Complete-transcript operation rebinds MUST persist a unique per-attempt claim
identity. Compensation MUST match that identity as well as the expected
generation and owner, so a caller whose transaction result is ambiguous cannot
roll back a concurrent winner at the same generation. The identity MUST follow
the replacement request into pre-dispatch cleanup.
Downgrading the rebind-claim migration MUST preserve the shared migration
ownership registry required by its parent revision, including an empty registry.

Grouped terminal delivery MUST preserve cancellation received while awaiting
sibling delivery barriers, including the early barrier after a successful
terminal append. Cancellation MUST propagate after the delivery barrier completes.

A terminal event MUST remain bound to its original session, instance, and
owner epoch while pending events drain. A same-generation ownership handoff
MUST NOT let the predecessor append its terminal event with successor credentials
or clear the successor's pending state.

When a complete replay-input snapshot is available, recovery MUST use it as
the fresh request input even if an upstream parent response is no longer
available.

When complete-transcript recovery is enabled, the proxy MUST durably record a
root Codex turn with a session-scoped operation identity even when its request
has no `previous_response_id`. This root record MUST include the request body
and terminal output needed to seed later bounded snapshots.

#### Scenario: Empty terminal output uses durable output-item events

- **WHEN** a completed operation has `response.output=[]` and ordered
  `response.output_item.done` events
- **THEN** the persisted transcript contains those output items in
  `output_index` order

#### Scenario: Missing terminal event remains ineligible

- **WHEN** output-item events exist but no `response.completed` event is durable
- **THEN** the operation MUST NOT be marked as a complete replay transcript

#### Scenario: Conflicting terminal lifecycle remains ineligible

- **WHEN** the durable spool contains two terminal completion events, sparse
  output indexes, or a terminal `output` value with a non-list shape
- **THEN** the operation MUST NOT be marked as a complete replay transcript

#### Scenario: Terminal echo preserves stable item identity

- **WHEN** streamed output items and a non-empty terminal output contain the
  same payload but different stable item identities
- **THEN** the operation MUST be rejected instead of replaying either version

#### Scenario: Omitted terminal status remains compatible

- **WHEN** a streamed output item has `status: "completed"` and the matching
  terminal echo omits `status`
- **THEN** the operation MAY be materialized using the streamed item

#### Scenario: Parent purge uses a retained replay snapshot

- **WHEN** the parent response chain is unavailable but the completed operation
  has a valid bounded replay-input snapshot
- **THEN** the proxy retries with a fresh unanchored `response.create` using
  that snapshot and the continuation input

#### Scenario: Snapshot construction is best effort

- **WHEN** snapshot construction is malformed or exceeds configured bounds
- **THEN** the live terminal response still completes and the operation remains
  ineligible for snapshot recovery
