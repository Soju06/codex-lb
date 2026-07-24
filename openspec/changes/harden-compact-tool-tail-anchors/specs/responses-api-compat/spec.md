## MODIFIED Requirements

### Requirement: Responses Lite follow-up transformations fail closed

The service MUST evaluate terminal compact-tail retention with required anchors
and trim markers included. Compact trimming MAY omit a complete terminal
non-state, non-side-effecting tool pair only when the pair plus required anchors
and trim markers cannot fit the upstream wire budget. A latest output anchored
by `previous_response_id` or a non-empty `conversation` remains required only when its matching call is absent
from supplied input. A supplied call matches an output only when both `call_id`
and the function/custom/apply-patch protocol variant are compatible. An
unmatched latest tool call and a terminal side-effecting tool call or matching
pair remain required compact context and MUST fail closed with
`responses_compact_input_too_large` when they cannot fit.

#### Scenario: Marker framing omits an otherwise fitting non-state tool pair

- **WHEN** a terminal non-state, non-side-effecting tool pair fits alone but
  exceeds the wire budget after required anchors and a trim marker are included
- **THEN** compact trimming omits the complete pair and emits a trim marker
- **AND** the marker does not claim omitted terminal context was preserved

#### Scenario: Self-contained anchored ordinary pair remains optional

- **WHEN** a compact request carries `previous_response_id` or a non-empty `conversation` and its latest
  ordinary tool output has a matching call in supplied input
- **THEN** compact trimming MAY omit the complete pair when it cannot fit

#### Scenario: Reused call ID from another tool variant does not satisfy continuity

- **WHEN** a compact request carries `previous_response_id` or a non-empty `conversation` and its latest tool
  output reuses the `call_id` of an incompatible function/custom/apply-patch
  call variant in supplied input
- **THEN** the latest output remains required as continuity from the previous response
- **AND** the incompatible supplied call is not retained as its pair

#### Scenario: Unpaired and side-effecting tails fail closed

- **WHEN** an unpaired continuity-anchored output, unmatched latest tool call,
  or side-effecting tail cannot fit the compact wire budget
- **THEN** the service returns `responses_compact_input_too_large`
