## ADDED Requirements

### Requirement: Reconstruct non-streaming Responses output from streamed item events
When serving non-streaming `/v1/responses`, the service MUST preserve output items emitted on upstream SSE item events even when the terminal `response.completed` or `response.incomplete` payload omits `response.output` or returns it as an empty list.

#### Scenario: Reasoning item emitted before terminal response
- **WHEN** upstream emits a reasoning or other output item on `response.output_item.done` and the terminal response omits `output`
- **THEN** the final non-streaming JSON response includes that output item in `output`

#### Scenario: Terminal response already includes output
- **WHEN** the terminal response already includes a non-empty `output` array
- **THEN** the service returns the terminal `output` array unchanged
