### Requirement: Materialize complete bridge transcript output

When complete-transcript recovery is enabled and a completed operation's
`response.completed.response.output` is empty, the proxy MUST reconstruct the
terminal output from ordered `response.output_item.done` events in the durable
operation spool before marking the output transcript complete.

The proxy MUST reject reconstruction when the terminal completion is missing,
an output item is malformed or unfinished, or the configured transcript bound
would be exceeded.

#### Scenario: Empty terminal output uses durable output-item events

- **WHEN** a completed operation has `response.output=[]` and ordered
  `response.output_item.done` events
- **THEN** the persisted transcript contains those output items in
  `output_index` order

#### Scenario: Missing terminal event remains ineligible

- **WHEN** output-item events exist but no `response.completed` event is durable
- **THEN** the operation MUST NOT be marked as a complete replay transcript
