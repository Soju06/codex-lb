## MODIFIED Requirements

### Requirement: Responses Lite follow-up transformations fail closed

The service MUST evaluate terminal compact-tail retention with required anchors
and trim markers included. Compact trimming MAY omit a terminal non-state,
non-side-effecting tool pair only when the pair plus required anchors and trim
markers cannot fit the upstream wire budget. A latest output anchored by
`previous_response_id` and an `apply_patch_call` or `apply_patch_call_output`
remain required compact context and MUST fail closed with
`responses_compact_input_too_large` when they cannot fit.

#### Scenario: Marker framing omits an otherwise fitting non-state tool pair

- **WHEN** a terminal non-state, non-side-effecting tool pair fits alone but
  exceeds the wire budget after required anchors and a trim marker are included
- **THEN** compact trimming omits the complete pair and emits a trim marker

#### Scenario: Anchored and side-effecting tails fail closed

- **WHEN** a terminal continuity-anchored output or apply-patch tail cannot
  fit the compact wire budget
- **THEN** the service returns `responses_compact_input_too_large`
