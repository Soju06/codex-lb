## ADDED Requirements

### Requirement: Materialize complete bridge transcript output

When complete-transcript recovery is enabled and a completed operation's
`response.completed.response.output` is empty, the proxy MUST reconstruct the
terminal output from ordered `response.output_item.done` events in the durable
operation spool before marking the output transcript complete.

The proxy MUST reject reconstruction when the terminal completion is missing,
an output item is malformed or unfinished, or the configured transcript bound
would be exceeded.

The proxy MUST reject reconstruction when more than one terminal completion is
present, when output-item indexes are sparse or reused, or when a present
terminal `output` value is not a list. When both streamed output items and a
non-empty terminal output are present, each item MUST have matching stable
identity fields (`id`, `call_id`, and `type` when provided) before payload
comparison; an omitted terminal `status` MAY be tolerated.

When complete-transcript recovery is enabled, the proxy MUST make a best-effort
attempt to persist a bounded, self-contained replay-input snapshot for each
completed operation. The snapshot MUST omit stale response anchors and
response-owned item IDs, and MUST NOT delay or fail the live response if it
cannot be built.

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
