## ADDED Requirements

### Requirement: Materialize complete bridge transcript output

When complete-transcript recovery is enabled and a completed operation's
`response.completed.response.output` is empty, the proxy MUST reconstruct the
terminal output from ordered `response.output_item.done` events in the durable
operation spool before marking the output transcript complete.

The proxy MUST reject reconstruction when the terminal completion is missing,
an output item is malformed or unfinished, or the configured transcript bound
would be exceeded.

When complete-transcript recovery is enabled, the proxy MUST make a best-effort
attempt to persist a bounded, self-contained replay-input snapshot for each
completed operation. The snapshot MUST omit stale response anchors and
response-owned item IDs, and MUST NOT delay or fail the live response if it
cannot be built.

When a complete replay-input snapshot is available, recovery SHOULD use it as
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

#### Scenario: Parent purge uses a retained replay snapshot

- **WHEN** the parent response chain is unavailable but the completed operation
  has a valid bounded replay-input snapshot
- **THEN** the proxy retries with a fresh unanchored `response.create` using
  that snapshot and the continuation input

#### Scenario: Snapshot construction is best effort

- **WHEN** snapshot construction is malformed or exceeds configured bounds
- **THEN** the live terminal response still completes and the operation remains
  ineligible for snapshot recovery
